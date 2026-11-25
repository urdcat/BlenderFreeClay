"""Verify navigation bindings and pivots through the owned Blender event loop."""
import bpy,sys,pathlib,json,traceback
from mathutils import Vector,Quaternion
from bpy_extras.view3d_utils import location_3d_to_region_2d,region_2d_to_origin_3d,region_2d_to_vector_3d
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
out=pathlib.Path(sys.argv[-1]);report={'version':bpy.app.version_string,'checks':[]}
step=0;start=None;before=None;pivot=None;wrong=None;original=None
cases=[('RIGHTMOUSE',False,False),('MIDDLEMOUSE',False,False),
       ('RIGHTMOUSE',True,False),('MIDDLEMOUSE',True,False),
       ('RIGHTMOUSE',False,True),('MIDDLEMOUSE',False,True)]
def save():
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
def tick():
    global step,start,before,pivot,wrong,original
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW');rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            if step==0:
                window.event_simulate(type='ESC',value='PRESS');window.event_simulate(type='ESC',value='RELEASE')
                freeclay.register();bpy.context.scene.freeclay.auto_detail=False
            case=(step-1)//8;phase=(step-1)%8
            if step>0 and case<len(cases):
                button,pan,cancel=cases[case];obj=bpy.context.object
                if phase==0:
                    if case==0:
                        bpy.ops.freeclay.open_clay(new_sphere=True)
                        original=[tuple(v.co) for v in bpy.context.object.data.vertices]
                    rv.view_rotation=Quaternion((1,0,0,0));rv.view_location=(.4,-.3,0)
                    rv.view_distance=5.5;rv.view_perspective='ORTHO'
                elif phase==1:
                    q=location_3d_to_region_2d(region,rv,Vector((.5,.2,1.39)))
                    start=(round(q.x)+region.x,round(q.y)+region.y)
                    xy=(start[0]-region.x,start[1]-region.y)
                    origin=region_2d_to_origin_3d(region,rv,xy);direction=region_2d_to_vector_3d(region,rv,xy)
                    hit,location,_,_=obj.ray_cast(origin,direction);assert hit
                    center=sum((Vector(p) for p in obj.bound_box),Vector())/8
                    pivot=location if button=='MIDDLEMOUSE' else center
                    wrong=center if button=='MIDDLEMOUSE' else location
                    before=(rv.view_rotation.copy(),rv.view_location.copy(),location_3d_to_region_2d(region,rv,pivot),location_3d_to_region_2d(region,rv,wrong))
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=start[0],y=start[1])
                elif phase==2:
                    window.event_simulate(type=button,value='PRESS',x=start[0],y=start[1],shift=pan)
                elif phase==3:
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=start[0]+100,y=start[1]+30,shift=pan)
                elif phase==4:
                    if cancel:window.event_simulate(type='ESC',value='PRESS',x=start[0]+100,y=start[1]+30)
                    window.event_simulate(type=button,value='RELEASE',x=start[0]+100,y=start[1]+30,shift=pan)
                elif phase==6:
                    angle=rv.view_rotation.rotation_difference(before[0]).angle
                    distance=(rv.view_location-before[1]).length
                    drift=(location_3d_to_region_2d(region,rv,pivot)-before[2]).length
                    wrong_drift=(location_3d_to_region_2d(region,rv,wrong)-before[3]).length
                    if cancel:assert angle<1e-5 and distance<1e-5
                    elif pan:assert angle<1e-5 and distance>.01
                    else:assert angle>.1 and drift<.1 and wrong_drift>1,(button,angle,drift,wrong_drift)
                    assert not any(op.bl_idname=='FREECLAY_OT_navigate' for op in window.modal_operators)
                    assert [tuple(v.co) for v in obj.data.vertices]==original
                    report['checks'].append(dict(button=button,pan=pan,cancel=cancel,pivot_drift_px=drift,other_pivot_drift_px=wrong_drift,passed=True))
            if case>=len(cases):report['passed']=True;save();return None
        step+=1;save();return .15 if step>1 else 1
    except Exception:
        report['passed']=False;report['error']=traceback.format_exc();save();return None
bpy.app.timers.register(tick,first_interval=2)
