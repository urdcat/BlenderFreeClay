"""Clipboard undo in an owned graphical Blender event loop."""
import bpy,sys,pathlib,json,traceback
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
out=pathlib.Path(sys.argv[-1]);report={'checks':[]};step=0
source_name=None;pasted_name=None;faces=0;selected=0
sys.stderr=(out/'python-stderr.log').open('w',encoding='utf8',buffering=1)
def save():(out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
def tick():
    global step,source_name,pasted_name,faces,selected
    try:
        window=bpy.context.window_manager.windows[0]
        area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
        region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(window=window,area=area,region=region):
            if step==0:
                window.event_simulate(type='ESC',value='PRESS');window.event_simulate(type='ESC',value='RELEASE')
                freeclay.register();bpy.context.scene.freeclay.auto_detail=False
            elif step==1:
                bpy.ops.freeclay.open_clay(new_sphere=True)
                obj=bpy.context.object;source_name=obj.name;faces=len(obj.data.polygons)
                bpy.ops.object.mode_set(mode='OBJECT')
                mask=obj.data.attributes.new('.sculpt_mask','FLOAT','POINT')
                for v in obj.data.vertices:mask.data[v.index].value=1 if v.co.x<0 else 0
                selected=sum(all(mask.data[i].value<.5 for i in p.vertices) for p in obj.data.polygons)
                bpy.ops.object.mode_set(mode='SCULPT');bpy.ops.ed.undo_push(message='Clipboard fixture')
            elif step==2:bpy.ops.freeclay.clipboard('EXEC_DEFAULT',True,action='COPY')
            elif step==3:
                assert bpy.context.scene.get('freeclay_clipboard');bpy.ops.ed.undo()
            elif step==4:
                assert not bpy.context.scene.get('freeclay_clipboard');bpy.ops.ed.redo()
            elif step==5:
                assert bpy.context.scene.get('freeclay_clipboard')
                report['checks'].append({'copy_undo_redo':True})
                bpy.ops.freeclay.clipboard('EXEC_DEFAULT',True,action='PASTE');pasted_name=bpy.context.object.name
            elif step==6:
                assert len(bpy.context.object.data.polygons)==selected;bpy.ops.ed.undo()
            elif step==7:
                assert bpy.data.objects.get(pasted_name) is None;bpy.ops.ed.redo()
            elif step==8:
                assert bpy.data.objects.get(pasted_name) is not None
                report['checks'].append({'paste_undo_redo':True})
                bpy.ops.freeclay.focus(name=source_name)
                bpy.ops.ed.undo_push(message='Partial cut fixture')
                bpy.ops.freeclay.clipboard('EXEC_DEFAULT',True,action='CUT')
            elif step==9:
                assert len(bpy.data.objects[source_name].data.polygons)==faces-selected;bpy.ops.ed.undo()
            elif step==10:
                assert len(bpy.data.objects[source_name].data.polygons)==faces;bpy.ops.ed.redo()
            elif step==11:
                assert len(bpy.data.objects[source_name].data.polygons)==faces-selected
                report['checks'].append({'partial_cut_undo_redo':True})
                bpy.ops.freeclay.focus(name=pasted_name);bpy.ops.ed.undo_push(message='Whole cut fixture')
                bpy.ops.freeclay.clipboard('EXEC_DEFAULT',True,action='CUT')
            elif step==12:
                assert bpy.data.objects[pasted_name].hide_get() and bpy.context.object is None;bpy.ops.ed.undo()
            elif step==13:
                assert not bpy.data.objects[pasted_name].hide_get()
                assert bpy.context.object.name==pasted_name and bpy.context.object.mode=='SCULPT'
                bpy.ops.ed.redo()
            elif step==14:
                assert bpy.data.objects[pasted_name].hide_get() and bpy.context.object is None
                report['checks'].append({'whole_cut_undo_redo':True})
                bpy.ops.freeclay.clipboard('EXEC_DEFAULT',True,action='PASTE')
            elif step==15:
                assert len(bpy.context.object.data.polygons)==selected
                bpy.context.scene['freeclay_test_clipboard_faces']=selected
                bpy.ops.wm.save_as_mainfile(filepath=str(out/'clipboard-tested.blend'))
                report['passed']=True;save();return None
        step+=1;save();return .3 if step>1 else 1
    except Exception:
        report['passed']=False;report['step']=step;report['error']=traceback.format_exc();save();return None
bpy.app.timers.register(tick,first_interval=2)
