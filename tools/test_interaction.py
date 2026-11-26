"""Owned GUI regression: common size reference, pinned preview, F and masking."""
import bpy,sys,pathlib,json,traceback,math
from mathutils import Vector,Quaternion
from bpy_extras.view3d_utils import location_3d_to_region_2d
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
from freeclay import interaction
out=pathlib.Path(sys.argv[-1]);report={'version':bpy.app.version_string,'checks':[]}
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
step=0;near=None;point=None;original=None;weights=None;anchor=None;start=None;obj_name=None
cases=[(kind,mode,perspective) for perspective in ('ORTHO','PERSP') for kind in ('GRAB','REDUCE','DRAW','SMOOTH','CREASE','MASK') for mode in ('SCREEN','MODEL')]
def save(): (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
def mask_values(obj):
    attr=obj.data.attributes.get('.sculpt_mask')
    return [v.value for v in attr.data] if attr else [0.]*len(obj.data.vertices)
def tick():
    global step,near,point,original,weights,anchor,start,obj_name
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW');rv=area.spaces.active.region_3d
        with bpy.context.temp_override(window=window,area=area,region=region):
            def event(kind,value='PRESS',dx=0,dy=0,**kwargs):
                window.event_simulate(type=kind,value=value,x=point[0]+dx,y=point[1]+dy,**kwargs)
            def at(p):
                q=location_3d_to_region_2d(region,rv,Vector(p));return round(q.x)+region.x,round(q.y)+region.y
            if step==0:
                window.event_simulate(type='ESC',value='PRESS');window.event_simulate(type='ESC',value='RELEASE')
                freeclay.register()
            elif step==1:
                bpy.context.scene.freeclay.auto_detail=False
                bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
                bpy.ops.freeclay.open_clay(new_sphere=True);obj_name=bpy.context.object.name
                assert abs(bpy.context.scene.freeclay.grab_strength-.35)<1e-6
                assert bpy.context.scene.freeclay.size_mode=='SCREEN'
                rv.view_rotation=Quaternion((1,0,0,0));rv.view_location=(0,0,0)
                area.spaces.active.show_region_ui=True
                if out.name.startswith('mask-short'):step=73
            elif 2<=step<74:
                case=(step-2)//3;phase=(step-2)%3
                kind,mode,perspective=cases[case]
                if phase==0:
                    bpy.ops.freeclay.brush(kind=kind)
                    if kind!='REDUCE':assert bpy.context.scene.tool_settings.sculpt.brush.sculpt_brush_type==kind
                    bpy.context.scene.freeclay.size_mode=mode
                    rv.view_perspective=perspective;rv.view_distance=5.5
                    interaction.remember(bpy.context,Vector((.4,.1,1.4)))
                elif phase==1:
                    near=(interaction.radius_world(bpy.context,interaction.last_anchor(bpy.context)),interaction.circles(bpy.context)[0][1])
                    rv.view_distance=11
                else:
                    far=(interaction.radius_world(bpy.context,interaction.last_anchor(bpy.context)),interaction.circles(bpy.context)[0][1])
                    if mode=='SCREEN':assert abs(far[1]-near[1])<.02 and far[0]>near[0]*1.5,(cases[case],near,far)
                    else:assert abs(far[0]-near[0])<1e-6 and far[1]<near[1]*.75,(cases[case],near,far)
                    for brush in bpy.data.brushes:
                        if brush.name.startswith('FreeClay '):assert brush.use_locked_size==('VIEW' if mode=='SCREEN' else 'SCENE')
                    report['checks'].append(dict(brush=kind,mode=mode,projection=perspective,near=near,far=far))
            elif step==74:
                bpy.ops.freeclay.brush(kind='GRAB');bpy.context.scene.freeclay.size_mode='SCREEN'
                rv.view_perspective='ORTHO';rv.view_distance=5.5
                bpy.context.scene.freeclay.symmetry=False
            elif step==75:
                point=at((.4,.1,1.4));event('MOUSEMOVE','NOTHING')
            elif step==76:
                # Change the size while the pointer is in the sidebar.
                anchor=interaction.last_anchor(bpy.context)
                sidebar=next(r for r in area.regions if r.type=='UI')
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=sidebar.x+30,y=sidebar.y+300)
                bpy.context.scene.freeclay.grab_radius_px=130
            elif step==77:
                assert (interaction.last_anchor(bpy.context)-anchor).length<1e-6
                assert abs(interaction.circles(bpy.context)[0][1]-130)<.02
                bpy.ops.screen.screenshot(filepath=str(out/'pinned-preview.png'))
                event('MOUSEMOVE','NOTHING')
            elif step==78:event('F')
            elif step==79:event('MOUSEMOVE','NOTHING',dx=90)
            elif step==80:
                assert bpy.context.scene.freeclay.grab_radius_px>200
                bpy.ops.screen.screenshot(filepath=str(out/'size-adjustment.png'))
                event('LEFTMOUSE',dx=90)
            elif step==81:
                event('LEFTMOUSE','RELEASE',dx=90)
                assert not interaction.adjusting
                start=bpy.context.scene.freeclay.grab_radius_px
                event('F')
            elif step==82:event('WHEELUPMOUSE')
            elif step==83:event('ESC')
            elif step==84:
                assert bpy.context.scene.freeclay.grab_radius_px==start and not interaction.adjusting
                report['checks'].append({'preview_pinned_in_sidebar':True,'F_confirm_and_wheel_cancel':True})
                bpy.ops.freeclay.brush(kind='MASK')
                brush=bpy.context.scene.tool_settings.sculpt.brush;brush.size=55;brush.strength=1
                bpy.context.scene.freeclay.symmetry=True
                point=at((.45,.1,math.sqrt(2.25-.45**2-.1**2)))
                original=[tuple(v.co) for v in bpy.context.object.data.vertices]
                bpy.ops.ed.undo_push(message='Mask fixture start')
                event('MOUSEMOVE','NOTHING')
            elif step==85:event('LEFTMOUSE')
            elif 86<=step<=91:event('MOUSEMOVE','NOTHING',dx=((step-86)%2)*20)
            elif step==92:event('LEFTMOUSE','RELEASE',dx=10)
            elif step==93:
                obj=bpy.context.object;weights=mask_values(obj)
                bpy.ops.screen.screenshot(filepath=str(out/'mask-diagnostic.png'))
                report['checks'].append({'mask_debug':{'brush':bpy.context.scene.tool_settings.sculpt.brush.name,
                    'type':bpy.context.scene.tool_settings.sculpt.brush.sculpt_brush_type,'mode':obj.mode,
                    'attributes':list(obj.data.attributes.keys()),'modal':[o.bl_idname for o in window.modal_operators]}})
                assert max(weights)>.999 and sum(w>.999 for w in weights)>5,max(weights)
                assert max(w for w,v in zip(weights,obj.data.vertices) if v.co.z<-.5)<1e-6
                assert [tuple(v.co) for v in obj.data.vertices]==original
                bpy.ops.screen.screenshot(filepath=str(out/'mask-painted.png'))
                bpy.ops.ed.undo()
            elif step==94:
                assert max(mask_values(bpy.context.object))<1e-6
                bpy.ops.ed.redo()
            elif step==95:
                assert max(mask_values(bpy.context.object))>.999
                bpy.ops.freeclay.brush(kind='GRAB')
                bpy.context.scene.freeclay.grab_radius_px=200
                bpy.context.scene.freeclay.symmetry=False
                point=at((.9,.1,math.sqrt(2.25-.9**2-.1**2)))
                obj=bpy.context.object;original=[tuple(v.co) for v in obj.data.vertices];weights=mask_values(obj)
                anchor=min(obj.data.vertices,key=lambda v:(v.co-Vector((.9,.1,math.sqrt(2.25-.9**2-.1**2)))).length_squared).index
                event('MOUSEMOVE','NOTHING')
            elif step==96:event('LEFTMOUSE')
            elif 97<=step<=102:event('MOUSEMOVE','NOTHING',dy=(step-97)*12)
            elif step==103:event('LEFTMOUSE','RELEASE',dy=60)
            elif step==104:
                obj=bpy.context.object
                protected=[(v.co-Vector(p)).length for v,p,w in zip(obj.data.vertices,original,weights) if w>=.999]
                assert len(protected)>5
                assert max(protected)<1e-5,max(protected)
                assert max((v.co-Vector(p)).length for v,p,w in zip(obj.data.vertices,original,weights) if w<.01)>.02
                q1=location_3d_to_region_2d(region,rv,Vector(original[anchor]));q2=location_3d_to_region_2d(region,rv,obj.data.vertices[anchor].co)
                assert abs(q2.y-q1.y-21)<1,(q2.y-q1.y)
                report['checks'].append({'mask_protected_vertices':len(protected),'max_masked_displacement':max(protected),'precision_drag_input_px':60,'precision_output_px':q2.y-q1.y,'mask_undo_redo':True})
                bpy.ops.ed.undo()
            elif step==105:
                undo_positions=[tuple(v.co) for v in bpy.context.object.data.vertices]
                assert len(undo_positions)==len(original)
                undo_error=max((Vector(a)-Vector(b)).length for a,b in zip(undo_positions,original))
                report['checks'].append({'masked_grab_undo_error':undo_error})
                assert undo_error<1e-6,undo_error
                bpy.ops.freeclay.brush(kind='MASK')
                point=at((.45,.1,math.sqrt(2.25-.45**2-.1**2)))
                event('MOUSEMOVE','NOTHING')
            elif step==106:event('LEFTMOUSE',ctrl=True)
            elif step==107:event('MOUSEMOVE','NOTHING',dx=15,ctrl=True)
            elif step==108:event('LEFTMOUSE','RELEASE',dx=15,ctrl=True)
            elif step==109:
                assert sum(mask_values(bpy.context.object))<sum(weights)-1
                assert [tuple(v.co) for v in bpy.context.object.data.vertices]==original
                bpy.ops.ed.undo()
            elif step==110:
                assert max(abs(a-b) for a,b in zip(weights,mask_values(bpy.context.object)))<1e-6
                event('LEFTMOUSE',shift=True)
            elif step==111:event('MOUSEMOVE','NOTHING',dx=15,shift=True)
            elif step==112:event('LEFTMOUSE','RELEASE',dx=15,shift=True)
            elif step==113:
                assert [tuple(v.co) for v in bpy.context.object.data.vertices]==original,'Shift on Mask changed geometry'
                assert max(abs(a-b) for a,b in zip(weights,mask_values(bpy.context.object)))>.001,'Shift did not soften mask'
                report['checks'].append({'mask_ctrl_erase_shift_soften':True})
                bpy.ops.ed.undo()
            elif step==114:
                assert max(abs(a-b) for a,b in zip(weights,mask_values(bpy.context.object)))<1e-6
                bpy.context.scene.freeclay.auto_detail=True
                bpy.ops.freeclay.brush(kind='DRAW')
                bpy.context.scene.tool_settings.sculpt.brush.size=100
                point=at((.65,.1,math.sqrt(2.25-.65**2-.1**2)))
                event('MOUSEMOVE','NOTHING')
            elif step==115:event('LEFTMOUSE')
            elif step==116:event('MOUSEMOVE','NOTHING',dx=20)
            elif step==117:event('LEFTMOUSE','RELEASE',dx=20)
            elif step==118:
                assert bpy.context.object.use_dynamic_topology_sculpting
                bpy.ops.freeclay.brush(kind='GRAB')
                from mathutils.kdtree import KDTree
                obj=bpy.context.object;tree=KDTree(len(obj.data.vertices))
                for v in obj.data.vertices:tree.insert(v.co,v.index)
                tree.balance()
                error=max(tree.find(Vector(p))[2] for p,w in zip(original,weights) if w>=.999)
                report['checks'].append({'dyntopo_mask_debug':{'vertices':len(obj.data.vertices),'max_mask':max(mask_values(obj)),'sum_mask':sum(mask_values(obj)),'protected_error':error}})
                assert error<1e-5,('Dyntopo changed protected vertices',error)
                weights=mask_values(obj)
                assert max(weights)>=.999
                report['checks'].append({'masked_draw_dyntopo':True,'protected_error':error})
                bpy.ops.freeclay.brush(kind='MASK');bpy.ops.paint.mask_flood_fill(mode='INVERT')
            elif step==119:
                flipped=mask_values(bpy.context.object)
                assert max(abs(a+b-1) for a,b in zip(weights,flipped))<1e-6
                bpy.ops.paint.mask_flood_fill(mode='VALUE',value=0)
            elif step==120:
                assert max(mask_values(bpy.context.object))==0
                report['checks'].append({'mask_invert_and_clear':True})
                report['passed']=True;save();return None
        step+=1;save();return .15 if step>1 else 1
    except Exception:
        report['passed']=False;report['error']=traceback.format_exc();save();return None
bpy.app.timers.register(tick,first_interval=2)
