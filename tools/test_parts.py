"""Owned Blender geometry tests for clipboard ranges and clay focus."""
import bpy,sys,pathlib,json,traceback
from mathutils import Vector,Matrix
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import freeclay
from freeclay.parts import LocalTransform
report={'checks':[]}
try:
    freeclay.register()
    if '--reload-clipboard' in sys.argv:
        clip=bpy.data.meshes[bpy.context.scene['freeclay_clipboard']]
        assert len(clip.polygons)==bpy.context.scene['freeclay_test_clipboard_faces']
        assert clip.use_fake_user and bpy.context.object.get('freeclay_part')
        report={'passed':True,'clipboard_faces':len(clip.polygons),'independent_reload':True}
        (ROOT/'verification'/'clipboard-reload.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps(report));sys.exit(0)
    window=bpy.context.window_manager.windows[0]
    area=next(a for a in window.screen.areas if a.type=='VIEW_3D');region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(window=window,area=area,region=region):
        bpy.context.scene.freeclay.auto_detail=False
        bpy.ops.freeclay.open_clay(new_sphere=True)
        source=bpy.context.object;source.matrix_world=Matrix.Translation((1,2,3))@Matrix.Rotation(.3,4,'Z')
        bpy.ops.object.mode_set(mode='OBJECT')
        mask=source.data.attributes.new('.sculpt_mask','FLOAT','POINT')
        for v in source.data.vertices:mask.data[v.index].value=1 if v.co.x<0 else 0
        original=[tuple(v.co) for v in source.data.vertices];faces=len(source.data.polygons)
        selected=sum(all(mask.data[i].value<.5 for i in p.vertices) for p in source.data.polygons)
        bpy.ops.object.mode_set(mode='SCULPT')
        assert bpy.ops.freeclay.clipboard(action='COPY')=={'FINISHED'}
        clip=bpy.data.meshes[bpy.context.scene['freeclay_clipboard']]
        assert len(clip.polygons)==selected and len(source.data.polygons)==faces
        assert [tuple(v.co) for v in source.data.vertices]==original
        assert bpy.ops.freeclay.clipboard(action='PASTE')=={'FINISHED'}
        pasted=bpy.context.object
        assert pasted!=source and len(pasted.data.polygons)==selected
        assert pasted.matrix_world==source.matrix_world and pasted.data.attributes.get('.sculpt_mask') is None
        assert bpy.context.scene.freeclay.transform_target=='OBJECT'
        bpy.ops.freeclay.focus(name=source.name)
        assert bpy.context.object==source and source.mode=='SCULPT'
        assert bpy.ops.freeclay.clipboard(action='CUT')=={'FINISHED'}
        assert len(source.data.polygons)==faces-selected
        assert len(pasted.data.polygons)==selected
        bpy.ops.freeclay.focus(name=pasted.name)
        assert bpy.ops.freeclay.clipboard(action='CUT')=={'FINISHED'}
        assert pasted.hide_get() and pasted.get('freeclay_cut') and bpy.context.object is None
        assert bpy.ops.freeclay.clipboard(action='PASTE')=={'FINISHED'}
        report['checks'].append({'range_copy_cut_paste':True,'original_faces':faces,'selected_faces':selected,'whole_cut_and_paste':True,'focus':True})
        bpy.ops.freeclay.open_clay(new_sphere=True);obj=bpy.context.object
        bpy.ops.object.mode_set(mode='OBJECT')
        attr=obj.data.attributes.new('.sculpt_mask','FLOAT','POINT')
        for v in obj.data.vertices:attr.data[v.index].value=1 if v.co.x<0 else 0
        original=[v.co.copy() for v in obj.data.vertices]
        for kind in ('ROTATE','SCALE'):
            deform=LocalTransform(obj.data,obj.matrix_world,Vector((.5,0,1.3)),1.5,False)
            deform.apply(kind,.3,Vector((0,0,1)))
            assert max((v.co-p).length for v,p in zip(obj.data.vertices,original) if p.x<0)<1e-7
            assert max((v.co-p).length for v,p in zip(obj.data.vertices,original) if p.x>0)>.01
            deform.apply(kind,0,Vector((0,0,1)))
            assert max((v.co-p).length for v,p in zip(obj.data.vertices,original))<1e-7
        report['checks'].append({'local_rotation_scale_mask_and_absolute_updates':True})
    report['passed']=True
except Exception:report['passed']=False;report['error']=traceback.format_exc()
(ROOT/'verification'/'parts.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
if not report['passed']:raise RuntimeError('Parts regression')
