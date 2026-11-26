"""Owned GUI: joined Grab -> native Smooth -> seam Reduce -> Undo/Redo."""
import bpy,sys,pathlib,json,traceback,math
from mathutils import Vector,Quaternion
from mathutils.bvhtree import BVHTree
from bpy_extras.view3d_utils import location_3d_to_region_2d
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
from freeclay import interaction
out=pathlib.Path(sys.argv[-1]);report={'checks':[]};step=0;point=None;before=None;after=None;band_before=0
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
def save():(out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
def snapshot():
    mesh=bpy.context.object.data
    return ([tuple(v.co) for v in mesh.vertices],[tuple(f.vertices) for f in mesh.polygons])
def band():return sum(abs(v.co.x)<.04 and -.1<v.co.y<1.7 and v.co.z>.5 for v in bpy.context.object.data.vertices)
def tick():
    global step,point,before,after,band_before
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW');rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            def event(type,value='PRESS',dy=0):window.event_simulate(type=type,value=value,x=point[0],y=point[1]+dy)
            def at(p):
                q=location_3d_to_region_2d(region,rv,Vector(p));return round(q.x)+region.x,round(q.y)+region.y
            if step==0:
                window.event_simulate(type='ESC',value='PRESS');window.event_simulate(type='ESC',value='RELEASE');freeclay.register()
            elif step==1:
                s=bpy.context.scene.freeclay;s.auto_detail=False
                bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
                bpy.ops.freeclay.open_clay(new_sphere=True);bpy.ops.freeclay.brush(kind='GRAB')
                s.symmetry=True;s.grab_strength=1;s.size_mode='SCREEN';s.grab_radius_px=100
                rv.view_rotation=Quaternion((1,0,0,0));rv.view_location=(0,.3,0);rv.view_distance=6;rv.view_perspective='ORTHO'
                bpy.ops.freeclay.display(kind='WIRE');before=snapshot()
            elif step==2:
                point=at((.2,0,math.sqrt(2.25-.04)));event('MOUSEMOVE','NOTHING')
            elif step==3:
                assert interaction.joined_outline(bpy.context,'GRAB') is not None
                bpy.ops.screen.screenshot(filepath=str(out/'joined-preview.png'))
                bpy.ops.ed.undo_push(message='Joined Grab fixture');event('LEFTMOUSE')
            elif 4<=step<=11:event('MOUSEMOVE','NOTHING',dy=(step-3)*15)
            elif step==12:event('LEFTMOUSE','RELEASE',dy=120)
            elif step==13:
                assert snapshot()!=before
                report['checks'].append({'joined_grab':True,'faces':len(bpy.context.object.data.polygons)})
                bpy.ops.freeclay.brush(kind='SMOOTH');bpy.context.scene.freeclay.auto_detail=True
                brush=bpy.context.scene.tool_settings.sculpt.brush;brush.size=180;brush.strength=.8
                point=at((0,.3,1.5));event('MOUSEMOVE','NOTHING')
            elif step==14:before=snapshot();event('LEFTMOUSE')
            elif 15<=step<=24:event('MOUSEMOVE','NOTHING',dy=((step-15)%6)*15)
            elif step==25:event('LEFTMOUSE','RELEASE',dy=60)
            elif step==26:
                assert bpy.context.object.use_dynamic_topology_sculpting
                bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                assert snapshot()!=before
                report['checks'].append({'native_smooth':True})
                bpy.ops.freeclay.brush(kind='REDUCE');s=bpy.context.scene.freeclay;s.radius=100;s.strength=1
                before=snapshot();band_before=band();point=at((.04,.15,1.5));event('MOUSEMOVE','NOTHING')
            elif step==27:
                assert interaction.joined_outline(bpy.context,'REDUCE') is not None
                bpy.ops.ed.undo_push(message='Seam Reduce fixture');event('LEFTMOUSE')
            elif 28<=step<=51:event('MOUSEMOVE','NOTHING',dy=((step-28)%12)*12)
            elif step==52:event('LEFTMOUSE','RELEASE',dy=132)
            elif step==53:
                after=snapshot();band_after=band()
                report['checks'].append({'seam_reduce_faces_before':len(before[1]),'faces_after':len(after[1]),'band_before':band_before,'band_after':band_after})
                assert len(after[1])<len(before[1]) and band_after<band_before
                polygons=after[1];tree=BVHTree.FromPolygons(after[0],polygons)
                assert not any(i<j and not set(polygons[i]).intersection(polygons[j]) for i,j in tree.overlap(tree))
                assert all(f.area>1e-12 for f in bpy.context.object.data.polygons)
                bpy.ops.ed.undo()
            elif step==54:assert snapshot()==before;bpy.ops.ed.redo()
            elif step==55:
                assert snapshot()==after
                report['checks'].append({'reduce_undo_redo':True,'no_degenerate_or_nonadjacent_crossing':True})
                bpy.ops.screen.screenshot(filepath=str(out/'seam-after.png'))
                bpy.ops.wm.save_as_mainfile(filepath=str(out/'seam-tested.blend'))
                report['passed']=True;save();return None
        step+=1;save();return .2 if step>1 else 1
    except Exception:report['passed']=False;report['step']=step;report['error']=traceback.format_exc();save();return None
bpy.app.timers.register(tick,first_interval=2)
