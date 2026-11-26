"""Clay parts, a mesh clipboard, and rigid/global or local brush transforms."""
import bpy,bmesh,math
import numpy as np
from mathutils import Vector,Matrix
from bpy.props import StringProperty,EnumProperty
from bpy_extras import view3d_utils as view
from . import interaction
from .grab import GrabDeformation


def is_part(obj):
    return obj.type=='MESH' and (obj.get('freeclay_part') or obj.get('freeclay_enabled')) and not obj.get('freeclay_cut')


class FREECLAY_OT_focus(bpy.types.Operator):
    bl_idname='freeclay.focus'
    bl_label='このClayを編集する'
    name:StringProperty()

    def execute(self,context):
        from . import load_brush,sync_symmetry
        obj=context.scene.objects.get(self.name)
        if obj is None or not is_part(obj):return {'CANCELLED'}
        if context.object and context.object.mode!='OBJECT':
            if context.object.use_dynamic_topology_sculpting:bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
            bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
        for other in context.selected_objects:other.select_set(False)
        obj.hide_set(False);obj.select_set(True);context.view_layer.objects.active=obj
        obj['freeclay_part']=True
        if 'freeclay_group' not in obj:obj['freeclay_group']=False
        obj['freeclay_enabled']=True
        bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='SCULPT')
        load_brush(context,'Grab','GRAB');sync_symmetry(context.scene.freeclay,context)
        interaction.redraw()
        return {'FINISHED'}


class FREECLAY_UL_parts(bpy.types.UIList):
    def filter_items(self,context,data,property):
        return [self.bitflag_filter_item if is_part(obj) else 0 for obj in getattr(data,property)],[]

    def draw_item(self,context,layout,data,item,icon,active_data,active_property,index=0):
        if 'freeclay_group' in item:layout.prop(item,'["freeclay_group"]',text='')
        layout.operator('freeclay.focus',text=item.name,depress=item==context.object,icon='MESH_DATA').name=item.name


class FREECLAY_OT_clipboard(bpy.types.Operator):
    bl_idname='freeclay.clipboard'
    bl_label='ClayのCopy / Cut / Paste'
    bl_description='マスクで保護していない面をコピー・切り取り、独立したClayとして貼り付けます'
    bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=[('COPY','Copy',''),('CUT','Cut',''),('PASTE','Paste','')])

    @classmethod
    def poll(cls,context):return context.area and context.area.type=='VIEW_3D'

    def execute(self,context):
        from . import Stroke,load_brush,sync_symmetry,reference_diameter
        if self.action=='PASTE':
            clip=bpy.data.meshes.get(context.scene.get('freeclay_clipboard',''))
            if clip is None:
                self.report({'WARNING'},'先にCopyまたはCutしてください');return {'CANCELLED'}
            if context.object and context.object.mode!='OBJECT':
                if context.object.use_dynamic_topology_sculpting:bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
                bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
            mesh=clip.copy();mesh.use_fake_user=False
            obj=bpy.data.objects.new('Clay Part',mesh);context.collection.objects.link(obj)
            values=context.scene.get('freeclay_clipboard_matrix',[v for row in Matrix.Identity(4) for v in row])
            obj.matrix_world=Matrix([values[i*4:i*4+4] for i in range(4)])
            for other in context.selected_objects:other.select_set(False)
            obj.select_set(True);context.view_layer.objects.active=obj
            obj['freeclay_enabled']=True;obj['freeclay_part']=True;obj['freeclay_group']=False
            reference_diameter(obj)
            bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='SCULPT')
            sculpt=context.scene.tool_settings.sculpt
            obj['freeclay_previous_symmetry']=[sculpt.use_symmetry_x,sculpt.use_symmetry_y,sculpt.use_symmetry_z]
            import json
            obj['freeclay_previous_detail']=json.dumps({k:getattr(sculpt,k) for k in ('detail_type_method','detail_refine_method','detail_percent')})
            load_brush(context,'Grab','GRAB');sync_symmetry(context.scene.freeclay,context)
            context.scene.freeclay.transform_target='OBJECT'
            interaction.redraw();return {'FINISHED'}
        obj=context.object
        if obj is None or not is_part(obj) or obj.mode!='SCULPT':return {'CANCELLED'}
        transaction=Stroke(context,build_reducer=False,checkpoint=True)
        bm=bmesh.new();piece=None
        try:
            bm.from_mesh(obj.data);bm.faces.ensure_lookup_table()
            mask=bm.verts.layers.float.get('.sculpt_mask')
            chosen=[f.index for f in bm.faces if not f.hide and all(not v.hide and (mask is None or v[mask]<.5) for v in f.verts)]
            if not chosen:
                transaction.finish(context,True)
                self.report({'WARNING'},'コピーできる未マスクの面がありません');return {'CANCELLED'}
            selected=set(chosen);piece=bm.copy();piece.faces.ensure_lookup_table()
            bmesh.ops.delete(piece,geom=[f for f in piece.faces if f.index not in selected],context='FACES')
            isolated=[v for v in piece.verts if not v.link_faces]
            if isolated:bmesh.ops.delete(piece,geom=isolated,context='VERTS')
            piece_mask=piece.verts.layers.float.get('.sculpt_mask')
            if piece_mask:piece.verts.layers.float.remove(piece_mask)
            clip=bpy.data.meshes.new('FreeClay Clipboard');piece.to_mesh(clip);clip.use_fake_user=True
            previous=context.scene.get('freeclay_clipboard','')
            context.scene['freeclay_clipboard']=clip.name
            context.scene['freeclay_clipboard_matrix']=[v for row in obj.matrix_world for v in row]
            # Keep the previous clipboard datablock for Blender's undo history.
            if previous in bpy.data.meshes:bpy.data.meshes[previous].use_fake_user=False
            whole=self.action=='CUT' and len(chosen)==len(bm.faces)
            if self.action=='CUT' and not whole:
                bmesh.ops.delete(bm,geom=[bm.faces[i] for i in chosen],context='FACES')
                bm.to_mesh(obj.data);obj.data.update();transaction.changed=True
            transaction.finish(context,False)
            if whole:
                bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
                obj['freeclay_cut']=True;obj.hide_set(True);obj.select_set(False)
                context.view_layer.objects.active=None
            self.report({'INFO'},f'{len(chosen):,} 面を'+('コピーしました' if self.action=='COPY' else '切り取りました'))
            interaction.redraw();return {'FINISHED'}
        except Exception:
            transaction.finish(context,True);raise
        finally:
            bm.free()
            if piece:piece.free()


class LocalTransform:
    def __init__(self,mesh,matrix,anchor,radius,symmetry):
        self.base=GrabDeformation(mesh,matrix,anchor,radius,symmetry)
        self.mesh=mesh

    def apply(self,kind,amount,axis):
        base=self.base;positions=base.original.copy()
        steps=max(1,math.ceil(abs(amount)/.04))
        inv=np.linalg.inv(base.linear);axis=Vector(axis).normalized()
        center=base.linear@base.anchor
        rotation=np.array(Matrix.Rotation(amount/steps,3,axis)) if kind=='ROTATE' else None
        for _ in range(steps):
            folded=positions.copy()
            if base.symmetry:folded[:,0]*=base.sides
            world=folded@base.linear.T-center
            r2=np.einsum('ij,ij->i',world,world)/base.radius**2
            weight=np.maximum(0,1-r2)**2*base.mobility
            difference=(world@rotation.T-world) if kind=='ROTATE' else world*(math.exp(amount/steps)-1)
            movement=(difference@inv.T)*weight[:,None]
            if base.symmetry:
                movement[:,0]*=base.sides
                seam=np.minimum(1,np.abs(positions[:,0])/max(abs(base.anchor[0]),1e-8))
                movement[:,0]*=seam
                x=positions[:,0];dx=movement[:,0]
                inward=x*dx<0
                movement[inward,0]=x[inward]/(1+np.abs(dx[inward])/np.maximum(np.abs(x[inward]),1e-12))-x[inward]
            positions+=movement
        self.mesh.vertices.foreach_set('co',positions.astype(np.float32).ravel());self.mesh.update()


class FREECLAY_OT_transform(bpy.types.Operator):
    bl_idname='freeclay.transform'
    bl_label='Clayを動かす'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    kind:EnumProperty(items=[('GRAB','移動',''),('ROTATE','回転',''),('SCALE','拡大縮小','')])

    @classmethod
    def poll(cls,context):return context.object and context.object.mode=='SCULPT' and is_part(context.object)

    def invoke(self,context,event):
        from . import Stroke
        self.area=context.area;self.region=context.region;self.rv=context.region_data
        if self.region.type!='WINDOW':return {'CANCELLED'}
        self.start=Vector((event.mouse_region_x,event.mouse_region_y));self.mouse=self.start.copy()
        self.whole=context.scene.freeclay.transform_target=='OBJECT';self.axis='NONE';self.stroke=None
        self.gain=context.scene.freeclay.grab_strength
        self.obj=context.object
        if self.whole:
            self.targets=[o for o in context.scene.objects if is_part(o) and (o==self.obj or o.get('freeclay_group'))]
            self.matrices=[o.matrix_world.copy() for o in self.targets]
            self.anchor=self.obj.matrix_world@(sum((Vector(p) for p in self.obj.bound_box),Vector())/8)
            if self.obj.use_dynamic_topology_sculpting:bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
            bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
        else:
            self.stroke=Stroke(context,build_reducer=False,checkpoint=True)
            self.anchor=interaction.anchor_at(context,self.start)
            radius=interaction.radius_world(context,self.anchor,self.kind)
            self.deform=LocalTransform(self.obj.data,self.obj.matrix_world,self.anchor,radius,context.scene.freeclay.symmetry)
        self.origin=view.region_2d_to_location_3d(self.region,self.rv,self.start,self.anchor)
        interaction.remember(context,self.anchor)
        self.area.header_text_set('部品全体: X/Y/Zでワールド軸指定 · Escで取消' if self.whole else '範囲内を変形 · Escで取消')
        context.window_manager.modal_handler_add(self);return {'RUNNING_MODAL'}

    def move(self):
        dx=self.mouse.x-self.start.x
        axis=Vector((0,0,1)) if self.axis=='NONE' else Vector(tuple(float(k==self.axis) for k in 'XYZ'))
        if self.whole:
            if self.kind=='GRAB':
                delta=(view.region_2d_to_location_3d(self.region,self.rv,self.mouse,self.anchor)-self.origin)*self.gain
                if self.axis!='NONE':
                    a=view.location_3d_to_region_2d(self.region,self.rv,self.anchor)
                    b=view.location_3d_to_region_2d(self.region,self.rv,self.anchor+axis)
                    projected=b-a if a is not None and b is not None else Vector((0,0))
                    if projected.length>1e-4:distance=(self.mouse-self.start).dot(projected.normalized())/projected.length
                    else:
                        unit=(view.region_2d_to_location_3d(self.region,self.rv,self.start+Vector((1,0)),self.anchor)-self.origin).length
                        distance=dx*unit
                    delta=axis*distance*self.gain
                transform=Matrix.Translation(delta)
            elif self.kind=='ROTATE':transform=Matrix.Translation(self.anchor)@Matrix.Rotation(dx*.01*self.gain,4,axis)@Matrix.Translation(-self.anchor)
            else:
                factor=math.exp(max(-5,min(5,dx/150*self.gain)))
                scale=[factor if self.axis=='NONE' or k==self.axis else 1 for k in 'XYZ']
                transform=Matrix.Translation(self.anchor)@Matrix.Diagonal((*scale,1))@Matrix.Translation(-self.anchor)
            for obj,matrix in zip(self.targets,self.matrices):obj.matrix_world=transform@matrix
        else:
            self.stroke.changed=True
            self.deform.apply(self.kind,dx*(.01 if self.kind=='ROTATE' else 1/150)*self.gain,self.rv.view_rotation@Vector((0,0,1)))
        self.area.tag_redraw()

    def finish(self,context,cancel):
        if self.whole:
            if cancel:
                for obj,matrix in zip(self.targets,self.matrices):obj.matrix_world=matrix
            bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='SCULPT')
        elif self.stroke:self.stroke.finish(context,cancel)
        self.area.header_text_set(None);self.area.tag_redraw()

    def modal(self,context,event):
        if event.type in {'ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'}:
            self.finish(context,True);return {'CANCELLED'}
        if event.type in {'X','Y','Z'} and event.value=='PRESS' and self.whole:
            self.axis=event.type if self.axis!=event.type else 'NONE';self.move()
        if event.type=='MOUSEMOVE':
            self.mouse=Vector((event.mouse_x-self.region.x,event.mouse_y-self.region.y));self.move()
        if event.type=='LEFTMOUSE' and event.value=='RELEASE':
            self.mouse=Vector((event.mouse_x-self.region.x,event.mouse_y-self.region.y));self.move()
            self.finish(context,False);return {'FINISHED'}
        return {'RUNNING_MODAL'}

    def cancel(self,context):self.finish(context,True)


class FREECLAY_WST_transform(bpy.types.WorkSpaceTool):
    bl_space_type='VIEW_3D';bl_context_mode='SCULPT'
    bl_idname='freeclay.transform_tool';bl_label='回転・拡大縮小';bl_icon='brush.sculpt.rotate'
    bl_keymap=(('freeclay.sculpt_stroke',{'type':'LEFTMOUSE','value':'PRESS','any':True},None),)
    @staticmethod
    def draw_cursor(context,tool,xy):interaction.capture_cursor(context,xy)
    @staticmethod
    def draw_settings(context,layout,tool):
        layout.prop(context.scene.freeclay,'transform_target',expand=True)
        layout.prop(context.scene.freeclay,'grab_strength',text='操作量')


def draw_panel(context,layout):
    box=layout.box();box.label(text='Clay部品 · 名前を押して編集')
    box.template_list('FREECLAY_UL_parts','',context.scene,'objects',context.scene.freeclay,'part_index',rows=3)
    row=box.row(align=True)
    for action in ('COPY','CUT','PASTE'):row.operator('freeclay.clipboard',text=action.title()).action=action
    box.label(text='未マスクの面をCopy/Cut')
    row=box.row(align=True);row.label(text='□: FIXでまとめて操作')
    row.operator('freeclay.open_clay',text='新規',icon='ADD').new_sphere=True


_classes=(FREECLAY_OT_focus,FREECLAY_UL_parts,FREECLAY_OT_clipboard,FREECLAY_OT_transform)
def register():
    for cls in _classes:bpy.utils.register_class(cls)
    bpy.utils.register_tool(FREECLAY_WST_transform,after={'freeclay.grab_tool'},group=False)
def unregister():
    bpy.utils.unregister_tool(FREECLAY_WST_transform)
    for cls in reversed(_classes):bpy.utils.unregister_class(cls)
