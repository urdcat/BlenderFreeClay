"""Drag local/global transforms on owned clay, including grouped parts and undo."""
import bpy,sys,pathlib,json,traceback,math
from mathutils import Vector,Quaternion
from bpy_extras.view3d_utils import location_3d_to_region_2d
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
out=pathlib.Path(sys.argv[-1]);report={'checks':[]};step=0
cases=[('GRAB','OBJECT',False),('ROTATE','OBJECT',False),('SCALE','OBJECT',False),('GRAB','OBJECT',True),('ROTATE','BRUSH',False),('SCALE','BRUSH',False)]
point=None;original=None;matrix=None;name=None;other=None;other_matrix=None;after=None
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
def save():(out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
def tick():
    global step,point,original,matrix,name,other,other_matrix,after
    try:
        window=bpy.context.window_manager.windows[0];area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW');rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            if step==0:
                window.event_simulate(type='ESC',value='PRESS');window.event_simulate(type='ESC',value='RELEASE')
                freeclay.register();bpy.context.scene.freeclay.auto_detail=False
            case=(step-1)//16;phase=(step-1)%16
            if step>0 and case<len(cases):
                kind,target,group=cases[case]
                if phase==0:
                    if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
                    bpy.ops.freeclay.open_clay(new_sphere=True)
                    obj=bpy.context.object;name=obj.name
                    bpy.context.scene.freeclay.symmetry=False
                    bpy.context.scene.freeclay.transform_target=target
                    other=None
                    if group:
                        bpy.ops.freeclay.clipboard(action='COPY');bpy.ops.freeclay.clipboard(action='PASTE')
                        other=obj.name;obj['freeclay_group']=True;obj.location.x=-3;bpy.context.view_layer.update();other_matrix=obj.matrix_world.copy()
                        obj=bpy.context.object;name=obj.name
                    bpy.ops.freeclay.brush(kind=kind)
                    rv.view_rotation=Quaternion((1,0,0,0));rv.view_location=(0,0,0);rv.view_distance=5.5;rv.view_perspective='ORTHO'
                    original=[tuple(v.co) for v in obj.data.vertices];matrix=obj.matrix_world.copy()
                elif phase==1:
                    q=location_3d_to_region_2d(region,rv,Vector((.55,.1,math.sqrt(2.25-.55**2-.1**2))))
                    point=(round(q.x)+region.x,round(q.y)+region.y)
                    bpy.ops.ed.undo_push(message='Transform fixture start')
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=point[0],y=point[1])
                elif phase==2:window.event_simulate(type='LEFTMOUSE',value='PRESS',x=point[0],y=point[1])
                elif phase==3 and target=='OBJECT':
                    window.event_simulate(type='X' if kind!='SCALE' else 'Z',value='PRESS',x=point[0],y=point[1])
                elif 4<=phase<=8:
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=point[0]+(phase-3)*12,y=point[1]+20)
                elif phase==9:window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=point[0]+60,y=point[1]+20)
                elif phase==10:
                    obj=bpy.data.objects[name]
                    assert obj.mode=='SCULPT'
                    if target=='OBJECT':
                        assert [tuple(v.co) for v in obj.data.vertices]==original
                        assert obj.matrix_world!=matrix
                        if kind=='GRAB':
                            delta=obj.matrix_world.translation-matrix.translation
                            assert delta.x>.05 and abs(delta.y)<1e-7 and abs(delta.z)<1e-7
                            if group:assert (bpy.data.objects[other].matrix_world.translation-other_matrix.translation-delta).length<1e-6
                        elif kind=='ROTATE':assert abs(obj.matrix_world.to_euler().x-.21)<1e-5
                        else:
                            scale=obj.matrix_world.to_scale();assert abs(scale.x-1)<1e-6 and abs(scale.y-1)<1e-6 and abs(scale.z-math.exp(.14))<1e-5
                    else:
                        assert obj.matrix_world==matrix and [tuple(v.co) for v in obj.data.vertices]!=original
                    after=(obj.matrix_world.copy(),[tuple(v.co) for v in obj.data.vertices])
                    report['checks'].append({'kind':kind,'target':target,'group':group,'drag':True})
                    if group:bpy.ops.screen.screenshot(filepath=str(out/'parts-arrangement.png'))
                    bpy.ops.ed.undo()
                elif phase==11:
                    obj=bpy.data.objects[name]
                    assert obj.matrix_world==matrix,(kind,'undo matrix',list(obj.matrix_world),list(matrix))
                    assert [tuple(v.co) for v in obj.data.vertices]==original,(kind,'undo mesh')
                    bpy.ops.ed.redo()
                elif phase==12:
                    obj=bpy.data.objects[name]
                    assert obj.matrix_world==after[0] and [tuple(v.co) for v in obj.data.vertices]==after[1]
                    report['checks'].append({'kind':kind,'target':target,'undo_redo':True})
                    window.event_simulate(type='LEFTMOUSE',value='PRESS',x=point[0],y=point[1])
                elif phase==13:window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=point[0]-40,y=point[1])
                elif phase==14:
                    window.event_simulate(type='ESC',value='PRESS',x=point[0]-40,y=point[1]);window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=point[0]-40,y=point[1])
                elif phase==15:
                    obj=bpy.data.objects[name]
                    assert obj.matrix_world==after[0] and [tuple(v.co) for v in obj.data.vertices]==after[1]
                    report['checks'].append({'kind':kind,'target':target,'cancel':True})
            if case>=len(cases):
                bpy.ops.wm.save_as_mainfile(filepath=str(out/'parts-tested.blend'))
                report['passed']=True;save();return None
        step+=1;save();return .15 if step>1 else 1
    except Exception:
        report['passed']=False;report['error']=traceback.format_exc();save();return None
bpy.app.timers.register(tick,first_interval=2)
