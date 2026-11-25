"""Measure Grab's actual pointer/vertex gain through Blender GUI events."""
import bpy,sys,pathlib,json,traceback,math
from mathutils import Vector,Quaternion
from bpy_extras.view3d_utils import location_3d_to_region_2d
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import freeclay
out=pathlib.Path(sys.argv[-1]); out.mkdir(exist_ok=True)
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
report={'version':bpy.app.version_string,'checks':[]}
step=0; case=0; start=None; origin=None; vertex_id=None; obj_name=None;original=None
cases=[('custom-low',.35,.8,False),('custom-full',1,.8,False),
       ('custom-wide',1,5,False),('custom-wide-symmetry',1,5,True),
       ('custom-maximum-range',1,20,True),('custom-cancel',1,5,True),
       ('center-straight',1,.3,True),('center-inward',1,.3,True),('center-outward',1,.3,True)]

def save():
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')

def tick():
    global step,case,start,origin,vertex_id,obj_name,original
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW')
        rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            if step==0:
                window.event_simulate(type='ESC',value='PRESS')
                window.event_simulate(type='ESC',value='RELEASE')
                freeclay.register()
                bpy.context.scene.freeclay.auto_detail=False
            phase=(step-1)%15
            case=(step-1)//15
            if step>0 and case<len(cases):
                label,strength,size_ratio,symmetry=cases[case]
                central=label.startswith('center-')
                motion=(0 if label=='center-straight' else -20 if label=='center-inward' else 20,60) if central else (60,0)
                if phase==0:
                    if bpy.context.object and bpy.context.object.mode!='OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
                    bpy.ops.freeclay.open_clay(new_sphere=True)
                    obj=bpy.context.object;obj_name=obj.name
                    bpy.context.scene.freeclay.symmetry=symmetry
                    rv.view_rotation=Quaternion((1,0,0,0));rv.view_location=(0,0,0)
                    rv.view_distance=5.5;rv.view_perspective='ORTHO'
                    bpy.ops.freeclay.brush(kind='GRAB')
                    bpy.context.scene.freeclay.grab_strength=strength
                    bpy.context.scene.freeclay.grab_diameter=size_ratio
                    original=[tuple(v.co) for v in obj.data.vertices]
                    px=.025 if central else .55
                    p=Vector((px,.1,math.sqrt(2.25-px**2-.1**2)))
                    vertex=min(obj.data.vertices,key=lambda v:(v.co-p).length_squared)
                    vertex_id=vertex.index;origin=vertex.co.copy()
                elif phase==1:
                    # Region projection matrices settle on the next redraw.
                    q=location_3d_to_region_2d(region,rv,origin)
                    start=(round(region.x+q.x),round(region.y+q.y))
                    bpy.ops.ed.undo_push(message='Grab measurement start')
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=start[0],y=start[1])
                elif phase==2:
                    window.event_simulate(type='LEFTMOUSE',value='PRESS',x=start[0],y=start[1])
                elif 3<=phase<=9:
                    fraction=(phase-3)/6
                    window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=round(start[0]+motion[0]*fraction),y=round(start[1]+motion[1]*fraction))
                elif phase==10:
                    if label=='custom-cancel':
                        obj=bpy.data.objects[obj_name]
                        assert (obj.data.vertices[vertex_id].co-origin).length>.01
                        window.event_simulate(type='ESC',value='PRESS',x=start[0]+60,y=start[1])
                    window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=start[0]+motion[0],y=start[1]+motion[1])
                elif phase==11:
                    obj=bpy.data.objects[obj_name]
                    a=location_3d_to_region_2d(region,rv,origin)
                    b=location_3d_to_region_2d(region,rv,obj.data.vertices[vertex_id].co)
                    displacement=b.y-a.y if central else b.x-a.x
                    gain=displacement/60
                    report['checks'].append({'case':label,'strength':strength,'diameter_ratio':size_ratio,
                        'symmetry':symmetry,'pointer_px':60,'vertex_px':displacement,'gain':gain})
                    if central:
                        from mathutils.bvhtree import BVHTree
                        mesh=obj.data
                        polygons=[tuple(f.vertices) for f in mesh.polygons]
                        tree=BVHTree.FromPolygons([v.co for v in mesh.vertices],polygons)
                        crossings=sum(i<j and not set(polygons[i]).intersection(polygons[j]) for i,j in tree.overlap(tree))
                        degenerate=sum(f.area<1e-12 for f in mesh.polygons)
                        collapsed=sum(abs(p[0])>1e-7 and abs(v.co.x)<1e-9 for v,p in zip(mesh.vertices,original))
                        report['checks'].append({'case':label,'nonadjacent_intersections':crossings,'degenerate':degenerate,'collapsed_to_seam':collapsed})
                        assert crossings==degenerate==collapsed==0
                    expected=0 if label=='custom-cancel' else strength
                    assert abs(gain-expected)<.03,(label,gain,expected)
                    assert obj.mode=='SCULPT' and not freeclay._active_grabs
                    if label=='custom-cancel':
                        assert [tuple(v.co) for v in obj.data.vertices]==original
                    else:
                        bpy.ops.ed.undo()
                elif phase==12 and label!='custom-cancel':
                    obj=bpy.data.objects[obj_name]
                    assert [tuple(v.co) for v in obj.data.vertices]==original,'Grab undo did not restore geometry'
                    report['checks'].append({'case':label,'undo_restores':True})
                    bpy.ops.ed.redo()
                elif phase==13 and label!='custom-cancel':
                    obj=bpy.data.objects[obj_name]
                    a=location_3d_to_region_2d(region,rv,origin)
                    b=location_3d_to_region_2d(region,rv,obj.data.vertices[vertex_id].co)
                    displacement=b.y-a.y if central else b.x-a.x
                    assert abs(displacement/60-strength)<.03,'Grab redo did not restore geometry'
                    report['checks'].append({'case':label,'redo_restores':True})
                elif phase==14 and label=='custom-full':
                    bpy.ops.screen.screenshot(filepath=str(out/'grab-controls.png'))
            if case>=len(cases):
                report['passed']=True;save();return None
        step+=1;save();return .15 if step>1 else 1
    except Exception:
        report['passed']=False;report['error']=traceback.format_exc();save();return None

bpy.app.timers.register(tick,first_interval=2)
