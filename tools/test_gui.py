"""Synthetic input via Blender's own API, on a worker-owned inactive desktop."""
import bpy, sys, pathlib, json, traceback, time, re
from mathutils import Quaternion, Vector
from bpy_extras.view3d_utils import location_3d_to_region_2d
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import freeclay

out=pathlib.Path(sys.argv[-1]) if '--' in sys.argv else ROOT/'verification'/'gui'
out.mkdir(exist_ok=True)
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
report={'version':bpy.app.version_string,'backend':'inactive desktop + Window.event_simulate', 'checks':[]}
step=0
points=[]
obj_name=None
initial=None
reduced=None
draw_before=None
rotation_before=None
workflow_before=None
workflow_faces=0
mirror_before_reduce=0


def mirror_error(mesh):
    from mathutils.kdtree import KDTree
    tree=KDTree(len(mesh.vertices))
    for v in mesh.vertices:
        tree.insert(v.co,v.index)
    tree.balance()
    return max(tree.find(Vector((-v.co.x,v.co.y,v.co.z)))[2] for v in mesh.vertices)


def mirror_surface_error(mesh):
    # Dyntopo may triangulate the two sides differently. Measure the surface,
    # not distance to a matching vertex, when judging native sculpt symmetry.
    from mathutils.bvhtree import BVHTree
    vertices=[v.co.copy() for v in mesh.vertices]
    tree=BVHTree.FromPolygons(vertices,[tuple(p.vertices) for p in mesh.polygons])
    samples=list(vertices)
    samples.extend(sum((vertices[i] for i in p.vertices),Vector())/len(p.vertices) for p in mesh.polygons)
    return max(tree.find_nearest(Vector((-p.x,p.y,p.z)))[3] for p in samples)


def save():
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')


def tick():
    global step, points, obj_name, initial, reduced, draw_before, rotation_before, workflow_before, workflow_faces, mirror_before_reduce
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW')
        rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            if step==0:
                window.event_simulate(type='ESC',value='PRESS')
                window.event_simulate(type='ESC',value='RELEASE')
                native_invoke=freeclay.FREECLAY_OT_reduce.invoke
                def traced_invoke(self,context,event):
                    report['checks'].append({'invoke':event.type,'region':context.region.type,
                                            'x':event.mouse_region_x,'y':event.mouse_region_y})
                    result=native_invoke(self,context,event)
                    report['checks'].append({'invoke_result':list(result)})
                    return result
                freeclay.FREECLAY_OT_reduce.invoke=traced_invoke
                freeclay.register()
                # Factory startup can already contain Sculpt settings. A genuinely
                # new scene reproduces the first-use state from the reported error.
                window.scene=bpy.data.scenes.new('FreeClay first-use GUI')
                assert bpy.context.scene.tool_settings.sculpt is None
                report['checks'].append({'sculpt_settings_initially_none':True})
                bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
                bpy.ops.freeclay.open_clay(new_sphere=True)
                assert area.spaces.active.overlay.show_stats
                bpy.ops.freeclay.display(kind='WIRE')
                assert area.spaces.active.shading.type=='SOLID' and area.spaces.active.overlay.show_wireframes
                obj=bpy.context.object; obj_name=obj.name
                initial=len(obj.data.polygons)
                rv.view_rotation=Quaternion((1,0,0,0))
                rv.view_location=Vector((0,0,0))
                rv.view_distance=5.5
                rv.view_perspective='ORTHO'
                area.spaces.active.show_region_ui=True
                bpy.context.scene.freeclay.radius=130
                bpy.context.scene.freeclay.strength=1
                bpy.context.scene.freeclay.preserve=.65
                bpy.ops.freeclay.brush(kind='REDUCE')
                bpy.ops.ed.undo_push(message='FreeClay test start')
                report['checks'].append({'setup':True,'initial_faces':initial})
            elif step==1:
                bpy.ops.screen.screenshot(filepath=str(out/'before-ui.png'))
                report['checks'].append({'tool':bpy.context.workspace.tools.from_space_view3d_mode('SCULPT').idname,
                                        'region':[region.x,region.y,region.width,region.height]})
                points=[]
                for i in range(25):
                    p=location_3d_to_region_2d(region,rv,Vector((-.55+i*.045,0,1.4)))
                    points.append((int(region.x+p.x),int(region.y+p.y)))
                x,y=points[0]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
            elif step==2:
                x,y=points[0]
                window.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y)
            elif 3<=step<28:
                if step==20:
                    live_stats=bpy.context.scene.statistics(bpy.context.view_layer)
                    assert int(re.search(r'Faces:([\d,]+)',live_stats).group(1).replace(',',''))==len(bpy.context.object.data.polygons)
                    assert len(bpy.context.object.data.polygons)<initial
                    report['checks'].append({'reduce_live_statistics':live_stats,
                                            'faces':len(bpy.context.object.data.polygons)})
                    bpy.ops.screen.screenshot(filepath=str(out/'reduce-live-stats.png'))
                if step==4:
                    report['checks'].append({'during_drag_mode':bpy.context.object.mode,
                                            'active_strokes':len(freeclay._active_strokes)})
                x,y=points[step-3]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
            elif step==28:
                x,y=points[-1]
                window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y)
            elif step==29:
                obj=bpy.data.objects[obj_name]
                reduced=len(obj.data.polygons)
                report['checks'].append({'drag_reduced':reduced<initial,'faces':reduced,
                                        'mode':obj.mode,'active_strokes':len(freeclay._active_strokes)})
                bpy.ops.screen.screenshot(filepath=str(out/'reduce-ui.png'))
                assert reduced<initial, 'GUI drag did not reduce'
                assert obj.mode=='SCULPT' and not freeclay._active_strokes
                bpy.ops.ed.undo()
            elif step==30:
                obj=bpy.data.objects[obj_name]
                count=len(obj.data.polygons)
                stats=bpy.context.scene.statistics(bpy.context.view_layer)
                if obj.use_dynamic_topology_sculpting:
                    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                    count=len(obj.data.polygons)
                    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                report['checks'].append({'undo_restores':count==initial,'faces':count,'mode':obj.mode,'stats':stats})
                assert count==initial, 'Undo did not restore pre-stroke mesh'
                bpy.ops.ed.redo()
            elif step==31:
                obj=bpy.data.objects[obj_name]
                count=len(obj.data.polygons)
                report['checks'].append({'redo_restores':count==reduced,'faces':count})
                assert count==reduced, 'Redo did not restore stroke'
                # Temporary Reduce through real keymap, then cancel the stroke.
                bpy.ops.freeclay.brush(kind='DRAW')
                x,y=points[0]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
                window.event_simulate(type='R',value='PRESS',x=x,y=y)
            elif step==32:
                x,y=points[0]
                window.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y)
            elif 33<=step<43:
                x,y=points[(step-33)*2]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
            elif step==43:
                x,y=points[20]
                window.event_simulate(type='ESC',value='PRESS',x=x,y=y)
                window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y)
                window.event_simulate(type='R',value='RELEASE',x=x,y=y)
            elif step==44:
                obj=bpy.data.objects[obj_name]
                count=len(obj.data.polygons)
                report['checks'].append({'temporary_cancel_restores':count==reduced,'faces':count,
                                        'mode':obj.mode,'active_strokes':len(freeclay._active_strokes)})
                assert count==reduced and obj.mode=='SCULPT' and not freeclay._active_strokes
                if obj.use_dynamic_topology_sculpting:
                    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                draw_before=[tuple(v.co) for v in obj.data.vertices]
                bpy.ops.freeclay.brush(kind='DRAW')
                x,y=points[0]
                window.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y)
            elif 45<=step<55:
                if step==53:
                    live_stats=bpy.context.scene.statistics(bpy.context.view_layer)
                    assert int(re.search(r'Tris:([\d,]+)',live_stats).group(1).replace(',',''))>reduced
                    report['checks'].append({'dyntopo_live_statistics':live_stats,
                                            'dyntopo':bpy.context.object.use_dynamic_topology_sculpting})
                    bpy.ops.screen.screenshot(filepath=str(out/'dyntopo-live-stats.png'))
                x,y=points[(step-45)*2]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
            elif step==55:
                x,y=points[20]
                window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y)
            elif step==56:
                obj=bpy.data.objects[obj_name]
                report['checks'].append({'auto_detail_resumed':obj.use_dynamic_topology_sculpting})
                assert obj.use_dynamic_topology_sculpting,'Dyntopo did not resume on next sculpt stroke'
                bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                changed=[tuple(v.co) for v in obj.data.vertices]!=draw_before
                report['checks'].append({'native_draw_changes_clay':changed,'faces':len(obj.data.polygons)})
                assert changed,'native Draw did not sculpt'
                bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                rotation_before=rv.view_rotation.copy()
                x,y=points[10]
                window.event_simulate(type='RIGHTMOUSE',value='PRESS',x=x,y=y)
            elif step==57:
                x,y=points[10]
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x+100,y=y+30)
            elif step==58:
                x,y=points[10]
                window.event_simulate(type='RIGHTMOUSE',value='RELEASE',x=x+100,y=y+30)
            elif step==59:
                rotated=rv.view_rotation.rotation_difference(rotation_before).angle>.1
                report['checks'].append({'right_drag_orbits':rotated})
                assert rotated,'RMB orbit did not rotate'
                # Exercise the user's actual loop on the same edited clay.
                points=[]
                for i in range(25):
                    p=location_3d_to_region_2d(region,rv,Vector((-.55+i*.045,0,1.4)))
                    points.append((int(region.x+p.x),int(region.y+p.y)))
            elif 60<=step<120:
                cycle,phase=divmod(step-60,15)
                name=('GRAB','SHIFT_SMOOTH','CREASE','REDUCE')[cycle]
                obj=bpy.data.objects[obj_name]
                shift=cycle==1
                if phase==0:
                    if obj.use_dynamic_topology_sculpting:
                        bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                    workflow_before=[tuple(v.co) for v in obj.data.vertices]
                    workflow_faces=len(obj.data.polygons)
                    if cycle==3:
                        mirror_before_reduce=mirror_error(obj.data)
                    if cycle in (0,2):
                        bpy.ops.freeclay.brush(kind=name)
                    if cycle==3:
                        x,y=points[0]
                        window.event_simulate(type='R',value='PRESS',x=x,y=y)
                elif phase==1:
                    x,y=points[0]
                    window.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y,shift=shift)
                elif 2<=phase<=10:
                    x,y=points[(phase-2)*2]
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y,shift=shift)
                elif phase==11:
                    x,y=points[16]
                    window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y,shift=shift)
                    if cycle==3:
                        window.event_simulate(type='R',value='RELEASE',x=x,y=y)
                elif phase==12:
                    if obj.use_dynamic_topology_sculpting:
                        bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                    changed=[tuple(v.co) for v in obj.data.vertices]!=workflow_before
                    faces=len(obj.data.polygons)
                    report['checks'].append({'workflow':name,'changed':changed,
                                            'faces_before':workflow_faces,'faces_after':faces,
                                            'mirror_error':mirror_error(obj.data)})
                    assert changed,name+' did not change clay'
                    if cycle==1:
                        tool=bpy.context.workspace.tools.from_space_view3d_mode('SCULPT')
                        assert tool.idname=='freeclay.grab_tool','Shift Smooth did not return to Grab'
                        report['checks'].append({'smooth_restores_grab_tool':True})
                    if cycle==3:
                        assert faces<workflow_faces,'Reduce after sculpt did not reduce'
            elif step==120:
                obj=bpy.data.objects[obj_name]
                error=mirror_error(obj.data)
                surface_error=mirror_surface_error(obj.data)
                report['checks'].append({'workflow_max_mirror_error':error,'before_reduce':mirror_before_reduce,
                                        'reduce_preserves_existing_symmetry':error<=mirror_before_reduce+1e-6,
                                        'workflow_mirror_surface_error':surface_error})
                assert error<=mirror_before_reduce+1e-6,'Reduce worsened existing symmetry'
                assert surface_error<.005,'Native sculpt surface symmetry deviation too large for this 3-unit fixture'
                for r in area.regions:
                    if r.type=='UI':
                        r.active_panel_category='FreeClay'
                bpy.ops.freeclay.brush(kind='REDUCE')
                area.tag_redraw()
            elif step==121:
                bpy.ops.screen.screenshot(filepath=str(out/'final-ui.png'))
                bpy.ops.wm.save_as_mainfile(filepath=str(out/'gui-tested.blend'))
                report['passed']=True
                save()
                return None
        step+=1
        save()
        return .15 if step>1 else 1.0
    except Exception:
        report['passed']=False
        report['error']=traceback.format_exc()
        save()
        return None

bpy.app.timers.register(tick,first_interval=2.0)
