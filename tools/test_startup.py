"""Start FreeClay in scenes whose sculpt settings have never been initialized."""
import bpy,sys,pathlib,json,traceback
ROOT=pathlib.Path(__file__).resolve().parents[1]
args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
sys.path.insert(0,args[1] if len(args)>1 else str(ROOT))
import freeclay
report={'version':bpy.app.version_string,'checks':[]}
try:
    freeclay.register()
    window=bpy.context.window_manager.windows[0]
    area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
    region=next(r for r in area.regions if r.type=='WINDOW')
    for new_sphere in (True,False):
        scene=bpy.data.scenes.new('Fresh FreeClay startup')
        window.scene=scene
        with bpy.context.temp_override(window=window,area=area,region=region):
            if not new_sphere:
                bpy.ops.mesh.primitive_cube_add()
                source=bpy.context.object
                source_geometry=[tuple(v.co) for v in source.data.vertices]
            assert scene.tool_settings.sculpt is None,'Fixture must not initialize sculpt settings'
            report['checks'].append({'new_sphere':new_sphere,'sculpt_settings_initially_none':True})
            assert bpy.ops.freeclay.open_clay(new_sphere=new_sphere)=={'FINISHED'}
            clay=bpy.context.object
            assert clay.mode=='SCULPT' and scene.tool_settings.sculpt is not None
            assert clay.get('freeclay_enabled') and clay.data.use_mirror_x
            assert clay.use_dynamic_topology_sculpting
            assert area.spaces.active.overlay.show_stats
            bpy.ops.freeclay.display(kind='WIRE')
            assert area.spaces.active.overlay.show_wireframes
            bpy.ops.freeclay.brush(kind='GRAB')
            bpy.ops.freeclay.brush(kind='REDUCE')
            assert bpy.ops.freeclay.leave()=={'FINISHED'}
            assert not clay.get('freeclay_enabled')
            if not new_sphere:
                assert bpy.context.object==source and not source.hide_get()
                assert [tuple(v.co) for v in source.data.vertices]==source_geometry
            # Re-entry must preserve the user's now-existing sculpt settings.
            sculpt=scene.tool_settings.sculpt
            sculpt.use_symmetry_x=False;sculpt.use_symmetry_y=True
            sculpt.detail_percent=17
            assert bpy.ops.freeclay.open_clay(new_sphere=True)=={'FINISHED'}
            assert bpy.ops.freeclay.leave()=={'FINISHED'}
            assert not sculpt.use_symmetry_x and sculpt.use_symmetry_y and sculpt.detail_percent==17
            report['checks'].append({'new_sphere':new_sphere,'start_leave_and_reentry':True})
    freeclay.unregister()
    report['passed']=True
except Exception:
    report['passed']=False;report['error']=traceback.format_exc()
destination=pathlib.Path(args[0]) if args else ROOT/'verification'/'startup.json'
destination.write_text(json.dumps(report,indent=2),encoding='utf8')
print(json.dumps(report))
if not report['passed']:raise RuntimeError('Fresh scene startup failed')
