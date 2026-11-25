"""FreeClay: a small, reversible clay workspace and conservative Reduce brush."""
bl_info = {'name':'FreeClay — Gentle Reduce', 'author':'FreeClay prototype',
           'version':(0,1,2), 'blender':(5,2,0), 'category':'Sculpt',
           'description':'Paint away excess topology without smoothing your shape'}

import math
import json
import time
import bpy
import bmesh
import gpu
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, EnumProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Quaternion
from .reducer import Reducer
from .grab import GrabDeformation

_keymaps = []
_active_strokes = set()
_active_grabs = set()
_grab_restore_timers = set()


def restore_grab_after_smooth(context):
    window=context.window;area=context.area;region=context.region;obj=context.object
    def restore():
        try:
            if any(op.bl_idname=='SCULPT_OT_brush_stroke' for op in window.modal_operators):
                return .03
            with bpy.context.temp_override(window=window,area=area,region=region):
                if bpy.context.object==obj and obj.mode=='SCULPT' and owned(bpy.context):
                    tool=bpy.context.workspace.tools.from_space_view3d_mode('SCULPT')
                    if tool and tool.idname=='freeclay.sculpt_tool':
                        bpy.ops.wm.tool_set_by_id(name='freeclay.grab_tool')
                        hide_shelf(bpy.context)
        except (ReferenceError,RuntimeError):
            pass
        _grab_restore_timers.discard(restore)
        return None
    _grab_restore_timers.add(restore)
    bpy.app.timers.register(restore,first_interval=.03)


def reference_diameter(obj):
    value=obj.get('freeclay_reference_diameter',0)
    if value<=0:
        corners=[obj.matrix_world@Vector(p) for p in obj.bound_box]
        value=max(max(p[i] for p in corners)-min(p[i] for p in corners) for i in range(3))
        value=max(value,.001)
        obj['freeclay_reference_diameter']=value
    return value


def sync_symmetry(self,context):
    if owned(context):
        sculpt=context.scene.tool_settings.sculpt
        sculpt.use_symmetry_x=self.symmetry
        sculpt.use_symmetry_y=False
        sculpt.use_symmetry_z=False
        # Mesh sculpt reads Mesh.use_mirror_*, not Paint's symmetry flags.
        context.object.data.use_mirror_x=self.symmetry
        context.object.data.use_mirror_y=False
        context.object.data.use_mirror_z=False


def hide_shelf(context):
    if context.area and context.area.type=='VIEW_3D' and context.space_data.show_region_asset_shelf:
        bpy.ops.screen.region_toggle(region_type='ASSET_SHELF')


class FreeClaySettings(bpy.types.PropertyGroup):
    grab_strength: FloatProperty(name='Grabの強さ',default=1,min=0,max=1,subtype='FACTOR',
                                 description='1.0でつかんだ頂点がカーソルに追従します（マスクと対称面の制約を除く）')
    grab_diameter: FloatProperty(name='Grabの範囲（初期サイズ比）',default=.8,min=.01,max=20,
                                 soft_min=.05,soft_max=20,precision=2,
                                 description='1.0＝開始時モデルの直径、5.0＝5倍。ズームで変わりません')
    grab_pressure: BoolProperty(name='Grabに筆圧を使う',default=False,
                               description='押し始めの筆圧で強さを調整します。オフなら設定した強さで引っ張ります')
    radius: IntProperty(name='大きさ', default=75, min=8, max=400, subtype='PIXEL')
    strength: FloatProperty(name='効き具合', default=0.55, min=0.05, max=1, subtype='FACTOR')
    preserve: FloatProperty(name='形を守る', default=0.8, min=0.1, max=1, subtype='FACTOR')
    pressure: BoolProperty(name='筆圧で効きを変える', default=True)
    auto_detail: BoolProperty(name='彫るときに細部を補う', default=True,
                             description='FreeClayの開始時に自動細分化を有効にします')
    symmetry: BoolProperty(name='左右対称', default=True,
                          update=sync_symmetry,
                          description='X面対称。Reduceでは左右の対応する辺を一組として減らします')


def owned(context):
    obj = context.object
    return bool(obj and obj.type=='MESH' and obj.get('freeclay_enabled',False))


def sculpt_context(context):
    return context.area and context.area.type=='VIEW_3D' and context.mode=='SCULPT' and owned(context)


def load_brush(context, name, kind):
    brush_name = 'FreeClay '+name
    brush = bpy.data.brushes.get(brush_name)
    if brush is None:
        brush = bpy.data.brushes.new(brush_name, mode='SCULPT')
        brush.sculpt_brush_type = kind
        brush.asset_mark()
        brush.use_fake_user = True
        brush.size = 75
        brush.strength = 0.35 if kind!='SMOOTH' else 0.45
        brush.spacing = 8
        brush.use_pressure_strength = True
        brush.use_pressure_size = False
        brush.use_space_attenuation = True
    bpy.ops.brush.asset_activate(asset_library_type='LOCAL', relative_asset_identifier='Brush/'+brush.name)
    bpy.ops.wm.tool_set_by_id(name='freeclay.grab_tool' if kind=='GRAB' else 'freeclay.sculpt_tool')
    if kind=='GRAB':
        reference_diameter(context.object)
    hide_shelf(context)
    return brush


class FREECLAY_OT_brush(bpy.types.Operator):
    bl_idname = 'freeclay.brush'
    bl_label = 'FreeClay Brush'
    kind: EnumProperty(items=[('DRAW','盛る',''),('GRAB','つかむ',''),('SMOOTH','ならす',''),
                              ('CREASE','溝',''),('REDUCE','軽くする','')], default='REDUCE')

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def execute(self,context):
        if self.kind == 'REDUCE':
            bpy.ops.wm.tool_set_by_id(name='freeclay.reduce_tool')
            hide_shelf(context)
        else:
            load_brush(context,self.kind.title(),self.kind)
        return {'FINISHED'}


class FREECLAY_OT_clay(bpy.types.Operator):
    bl_idname = 'freeclay.open_clay'
    bl_label = '粘土として開く（コピー）'
    bl_description = '元のモデルを残し、表示中の形をスカルプト用コピーにします'
    bl_options = {'REGISTER','UNDO'}
    new_sphere: BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls,context):
        return context.area and context.area.type=='VIEW_3D' and context.mode in {'OBJECT','SCULPT'}

    def execute(self,context):
        source = context.object
        if not self.new_sphere and (source is None or source.type!='MESH'):
            self.report({'WARNING'},'メッシュを選択するか「新しい粘土」を使ってください')
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if self.new_sphere:
            mesh=bpy.data.meshes.new('FreeClay Sphere')
            bm=bmesh.new()
            bmesh.ops.create_icosphere(bm,subdivisions=6,radius=1.5)
            bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),
                                direction='-X',dist=1e-6)
            bmesh.ops.triangulate(bm,faces=list(bm.faces))
            bm.to_mesh(mesh); bm.free()
            obj=bpy.data.objects.new('FreeClay Sphere',mesh)
            context.collection.objects.link(obj)
        else:
            evaluated=source.evaluated_get(context.evaluated_depsgraph_get())
            mesh=bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True,
                                               depsgraph=context.evaluated_depsgraph_get())
            obj=bpy.data.objects.new(source.name+' — Clay',mesh)
            context.collection.objects.link(obj)
            obj.matrix_world=source.matrix_world.copy()
            obj['freeclay_source']=source.name
            obj['freeclay_source_hidden']=source.hide_get()
            source.hide_set(True)
            bm=bmesh.new(); bm.from_mesh(mesh)
            bmesh.ops.triangulate(bm,faces=list(bm.faces))
            bm.to_mesh(mesh); bm.free()
        for old in context.selected_objects:
            old.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active=obj
        obj['freeclay_enabled']=True
        context.view_layer.update()
        reference_diameter(obj)
        sculpt=context.scene.tool_settings.sculpt
        obj['freeclay_previous_symmetry']=[sculpt.use_symmetry_x,sculpt.use_symmetry_y,sculpt.use_symmetry_z]
        obj['freeclay_previous_detail']=json.dumps({name:getattr(sculpt,name) for name in
                                                   ('detail_type_method','detail_refine_method','detail_percent')})
        space=context.space_data
        saved={}
        saved['show_region_toolbar']=space.show_region_toolbar
        space.show_region_toolbar=False
        saved['show_region_asset_shelf']=space.show_region_asset_shelf
        if space.show_region_asset_shelf:
            bpy.ops.screen.region_toggle(region_type='ASSET_SHELF')
        for name in ('show_floor','show_axis_x','show_axis_y','show_stats','show_outline_selected'):
            saved['overlay.'+name]=getattr(space.overlay,name); setattr(space.overlay,name,False)
        obj['freeclay_view_before']=json.dumps(saved)
        space.show_region_ui=True
        area=context.area
        window=context.window
        def select_panel():
            try:
                region_window=next(r for r in area.regions if r.type=='WINDOW')
                with bpy.context.temp_override(window=window,area=area,region=region_window):
                    hide_shelf(bpy.context)
                for region in area.regions:
                    if region.type=='UI':
                        region.active_panel_category='FreeClay'
            except (ReferenceError,TypeError,ValueError,StopIteration):
                pass
        bpy.app.timers.register(select_panel,first_interval=.2)
        for face in mesh.polygons:
            face.use_smooth=True
        bpy.ops.object.mode_set(mode='SCULPT')
        # No preference changes; brushes, mode and keymaps are local to FreeClay.
        load_brush(context,'Draw','DRAW')
        sync_symmetry(context.scene.freeclay,context)
        if context.scene.freeclay.auto_detail:
            sculpt.detail_type_method='BRUSH'
            sculpt.detail_refine_method='SUBDIVIDE'
            sculpt.detail_percent=25
            bpy.ops.sculpt.dynamic_topology_toggle()
        return {'FINISHED'}


class FREECLAY_OT_leave(bpy.types.Operator):
    bl_idname='freeclay.leave'
    bl_label='通常操作へ戻る'
    bl_description='FreeClayの操作割当を外し、元モデルの表示を戻します。粘土コピーは残ります'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def execute(self,context):
        obj=context.object
        if obj.use_dynamic_topology_sculpting:
            bpy.ops.sculpt.dynamic_topology_toggle()
        bpy.ops.wm.tool_set_by_id(name='builtin.brush')
        obj['freeclay_enabled']=False
        for key,value in json.loads(obj.get('freeclay_view_before','{}')).items():
            owner=context.space_data.overlay if key.startswith('overlay.') else context.space_data
            if key=='show_region_asset_shelf':
                if owner.show_region_asset_shelf!=value:
                    bpy.ops.screen.region_toggle(region_type='ASSET_SHELF')
            else:
                setattr(owner,key.split('.')[-1],value)
        sculpt=context.scene.tool_settings.sculpt
        for axis,value in zip('xyz',obj.get('freeclay_previous_symmetry',[False]*3)):
            setattr(sculpt,'use_symmetry_'+axis,value)
        for name,value in json.loads(obj.get('freeclay_previous_detail','{}')).items():
            setattr(sculpt,name,value)
        source=bpy.data.objects.get(obj.get('freeclay_source',''))
        if source:
            source.hide_set(obj.get('freeclay_source_hidden',False))
            obj.hide_set(True)
            bpy.ops.object.mode_set(mode='OBJECT')
            obj.select_set(False)
            source.select_set(True)
            context.view_layer.objects.active=source
        elif obj.mode=='SCULPT':
            bpy.ops.object.mode_set(mode='OBJECT')
        return {'FINISHED'}


class FREECLAY_OT_symmetrize(bpy.types.Operator):
    bl_idname='freeclay.symmetrize'
    bl_label='左右の形を揃える（−X → ＋X）'
    bl_description='粘土コピーの−X側を＋X側へ写し、Reduce用の対応する面を作ります'
    bl_options={'REGISTER','UNDO'}

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def execute(self,context):
        sculpt=context.scene.tool_settings.sculpt
        previous=sculpt.symmetrize_direction
        try:
            sculpt.symmetrize_direction='NEGATIVE_X'
            bpy.ops.sculpt.symmetrize()
        finally:
            sculpt.symmetrize_direction=previous
        return {'FINISHED'}


class Stroke:
    """Transaction shared by interactive brush and headless integration tests."""
    def __init__(self,context,build_reducer=True):
        self.obj=context.object
        self.mesh=self.obj.data
        self.was_sculpt=self.obj.mode=='SCULPT'
        self.was_dyntopo=self.obj.use_dynamic_topology_sculpting
        self.backup=None
        self.reducer=None
        self.changed=False
        if self.mesh.shape_keys or self.mesh.users>1:
            raise ValueError('「粘土として開く（コピー）」で編集用の粘土を作ってください')
        if any(m.show_viewport for m in self.obj.modifiers):
            raise ValueError('モディファイア付きモデルは「粘土として開く（コピー）」を使ってください')
        if self.was_dyntopo:
            bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
        if self.was_sculpt:
            bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
        try:
            self.backup=self.mesh.copy()
            if build_reducer:
                self.reducer=Reducer(self.mesh,self.obj.matrix_world)
        except Exception:
            self.finish(context,cancel=True)
            raise

    def dab(self,center,radius,direction,strength,preserve,symmetry=False):
        results=[self.reducer.dab(center,radius,direction,strength,preserve,mirror_x=symmetry)]
        if any(r['collapsed'] or r.get('triangulated',0) for r in results):
            self.changed=True
            self.reducer.write_mesh(self.mesh)
        return results

    def finish(self,context,cancel=False):
        try:
            if cancel and self.backup and self.changed:
                bm=bmesh.new(); bm.from_mesh(self.backup)
                bm.to_mesh(self.mesh); bm.free(); self.mesh.update()
            if self.was_sculpt and self.obj.mode!='SCULPT':
                bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='SCULPT')
                hide_shelf(context)
            if cancel and self.was_dyntopo and not self.obj.use_dynamic_topology_sculpting:
                bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
        finally:
            if self.reducer:
                self.reducer.close(); self.reducer=None
            if self.backup:
                bpy.data.meshes.remove(self.backup); self.backup=None


class FREECLAY_OT_reduce(bpy.types.Operator):
    bl_idname='freeclay.reduce'
    bl_label='軽くする'
    bl_description='なぞって余分な面を取り除きます。Escでこのストロークを取り消します'
    bl_options={'REGISTER','UNDO','BLOCKING'}
    temporary: BoolProperty(default=False,options={'HIDDEN'})

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def invoke(self,context,event):
        self._context_area=context.area
        self._region=context.region
        self._rv3d=context.region_data
        if self._region.type!='WINDOW' or self._rv3d is None:
            return {'CANCELLED'}
        self._stroke=None
        self._timer=None
        self._draw=None
        self._mouse=Vector((event.mouse_region_x,event.mouse_region_y))
        self._last=None
        self._pressed=not self.temporary
        self._pressure=1.0
        self._last_tick=0.0
        self._area_pointer=context.area.as_pointer()
        self._region_pointer=context.region.as_pointer()
        self._draw=bpy.types.SpaceView3D.draw_handler_add(self._draw_cursor,(context,), 'WINDOW','POST_PIXEL')
        self._timer=context.window_manager.event_timer_add(0.025,window=context.window)
        self._wm=context.window_manager
        context.window_manager.modal_handler_add(self)
        _active_strokes.add(self)
        context.area.header_text_set('軽くする  ·  なぞって減らす  ·  ホイール: 大きさ  ·  Esc: この一筆を戻す')
        if self._pressed:
            try:
                self._begin(context)
                self._paint(context,event)
            except Exception as exc:
                self.report({'ERROR'},str(exc)); self._end(context,True)
                return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _begin(self,context):
        if self._stroke is None:
            self._stroke=Stroke(context)

    def _paint(self,context,event=None):
        settings=context.scene.freeclay
        if event and getattr(event,'is_tablet',False) and settings.pressure:
            self._pressure=max(0.02,event.pressure)
        origin=view3d_utils.region_2d_to_origin_3d(self._region,self._rv3d,self._mouse)
        direction=view3d_utils.region_2d_to_vector_3d(self._region,self._rv3d,self._mouse)
        location,normal,_,_=self._stroke.reducer.raycast(origin,direction)
        if location is None:
            self._last=None
            return
        edge=view3d_utils.region_2d_to_location_3d(self._region,self._rv3d,
                                                 self._mouse+Vector((settings.radius,0)),location)
        radius=(edge-location).length
        if self._last is not None and (self._mouse-self._last).length < settings.radius*0.12:
            return
        self._stroke.dab(location,radius,direction,settings.strength*self._pressure,
                         settings.preserve,settings.symmetry)
        self._last=self._mouse.copy()
        self._last_tick=time.perf_counter()
        self._context_area.tag_redraw()

    def modal(self,context,event):
        try:
            if event.type in {'ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'}:
                self._end(context,True)
                return {'CANCELLED'}
            if self.temporary and event.type=='R' and event.value=='RELEASE':
                self._end(context,False)
                return {'FINISHED'}
            if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'}:
                settings=context.scene.freeclay
                settings.radius=round(settings.radius*(1.1 if event.type=='WHEELUPMOUSE' else 1/1.1))
                self._context_area.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type=='LEFTMOUSE':
                if event.value=='PRESS':
                    self._pressed=True; self._last=None; self._begin(context)
                    self._paint(context,event)
                elif event.value=='RELEASE':
                    # One undo step per completed drag, also for temporary Reduce.
                    if self._stroke:
                        self._mouse=Vector((event.mouse_x-self._region.x,event.mouse_y-self._region.y))
                        self._paint(context,event)
                    self._end(context,False)
                    return {'FINISHED'}
                return {'RUNNING_MODAL'}
            if event.type in {'MOUSEMOVE','INBETWEEN_MOUSEMOVE'}:
                self._mouse=Vector((event.mouse_x-self._region.x,event.mouse_y-self._region.y))
                if getattr(event,'is_tablet',False):
                    self._pressure=max(0.02,event.pressure) if context.scene.freeclay.pressure else 1.0
                if self._pressed and self._stroke and time.perf_counter()-self._last_tick>.020:
                    self._paint(context,event)
                self._context_area.tag_redraw()
            if event.type=='TIMER' and self._pressed and self._stroke:
                self._paint(context)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'},'FreeClay: '+str(exc))
            self._end(context,True)
            return {'CANCELLED'}

    def _draw_cursor(self,context):
        # Handlers are global; only paint in the invoking region.
        if (bpy.context.area is None or bpy.context.area.as_pointer()!=self._area_pointer
                or bpy.context.region.as_pointer()!=self._region_pointer):
            return
        radius=context.scene.freeclay.radius
        pts=[(self._mouse.x+math.cos(i*math.tau/64)*radius,
              self._mouse.y+math.sin(i*math.tau/64)*radius) for i in range(65)]
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        try:
            shader.bind(); shader.uniform_float('color',(0.35,0.9,0.72,0.85))
            batch_for_shader(shader,'LINE_STRIP',{'pos':pts}).draw(shader)
        finally:
            gpu.state.blend_set('NONE')

    def _end(self,context,cancel):
        try:
            if self._stroke:
                self._stroke.finish(context,cancel)
                self._stroke=None
        finally:
            if self._timer:
                self._wm.event_timer_remove(self._timer); self._timer=None
            if self._draw:
                bpy.types.SpaceView3D.draw_handler_remove(self._draw,'WINDOW'); self._draw=None
            self._context_area.header_text_set(None)
            self._context_area.tag_redraw()
            _active_strokes.discard(self)

    def cancel(self,context):
        self._end(context,True)


class FREECLAY_OT_navigate(bpy.types.Operator):
    bl_idname='freeclay.navigate'
    bl_label='粘土を回す'
    bl_options={'BLOCKING'}
    pan: BoolProperty(default=False)
    cursor_pivot: BoolProperty(default=False)

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def invoke(self,context,event):
        self.rv=context.region_data
        if self.rv is None:
            return {'CANCELLED'}
        self.start=Vector((event.mouse_x,event.mouse_y))
        self.button=event.type
        self.rotation=self.rv.view_rotation.copy()
        self.location=self.rv.view_location.copy()
        self.distance=self.rv.view_distance
        # Aim orbit at the touched surface, leaving the camera stationary initially.
        obj=context.object
        point=(event.mouse_region_x,event.mouse_region_y)
        origin=view3d_utils.region_2d_to_origin_3d(context.region,self.rv,point)
        direction=view3d_utils.region_2d_to_vector_3d(context.region,self.rv,point)
        inv=obj.matrix_world.inverted()
        hit,location,_,_=obj.ray_cast(inv@origin,(inv.to_3x3()@direction).normalized())
        self.pivot=(obj.matrix_world@location if hit else self.location.copy()) if self.cursor_pivot else obj.matrix_world@(sum((Vector(p) for p in obj.bound_box),Vector())/8)
        self.camera=self.location+self.rotation@Vector((0,0,self.distance))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self,context,event):
        if event.type=='ESC':
            self.rv.view_rotation=self.rotation
            self.rv.view_location=self.location
            return {'CANCELLED'}
        if event.type==self.button and event.value=='RELEASE':
            return {'FINISHED'}
        if event.type=='MOUSEMOVE':
            delta=Vector((event.mouse_x,event.mouse_y))-self.start
            if self.pan:
                scale=self.distance/max(context.region.height,1)*1.3
                self.rv.view_location=self.location-self.rotation@Vector((delta.x*scale,delta.y*scale,0))
            else:
                yaw=Quaternion((0,1,0),-delta.x*0.007)
                pitch=Quaternion((1,0,0),delta.y*0.007)
                local=yaw@pitch
                rotation=self.rotation@local
                world_delta=rotation@self.rotation.inverted()
                camera=self.pivot+world_delta@(self.camera-self.pivot)
                self.rv.view_rotation=rotation
                self.rv.view_location=camera-rotation@Vector((0,0,self.distance))
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class FREECLAY_WST_reduce(bpy.types.WorkSpaceTool):
    bl_space_type='VIEW_3D'
    bl_context_mode='SCULPT'
    bl_idname='freeclay.reduce_tool'
    bl_label='軽くする'
    bl_description='形を保ちながら、なぞって軽くする'
    bl_icon='brush.sculpt.simplify'
    bl_keymap=(('freeclay.reduce',{'type':'LEFTMOUSE','value':'PRESS'},None),)

    @staticmethod
    def draw_settings(context,layout,tool):
        s=context.scene.freeclay
        layout.prop(s,'radius'); layout.prop(s,'strength'); layout.prop(s,'preserve')

    @staticmethod
    def draw_cursor(context,tool,xy):
        if not _active_strokes and owned(context):
            from gpu_extras.presets import draw_circle_2d
            draw_circle_2d(xy,(0.35,0.9,0.72,0.85),context.scene.freeclay.radius,segments=64)


class FREECLAY_OT_sculpt_stroke(bpy.types.Operator):
    bl_idname='freeclay.sculpt_stroke'
    bl_label='粘土を彫る'

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def invoke(self,context,event):
        brush=context.scene.tool_settings.sculpt.brush
        if brush and brush.sculpt_brush_type=='GRAB' and not event.shift:
            result=bpy.ops.freeclay.grab('INVOKE_DEFAULT')
            return {'FINISHED'} if 'RUNNING_MODAL' in result else result
        temporary_grab=bool(brush and brush.sculpt_brush_type=='GRAB' and event.shift)
        if temporary_grab:
            # Native Smooth requires a brush-enabled tool. Restore the compact
            # Grab tool after Blender finishes its own modal stroke.
            bpy.ops.wm.tool_set_by_id(name='freeclay.sculpt_tool')
        # Keep Reduce's undo checkpoint in the regular mesh undo domain.
        # Re-enter Dyntopo only when the next actual sculpt stroke begins.
        if context.scene.freeclay.auto_detail and not context.object.use_dynamic_topology_sculpting:
            bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
        try:
            result=bpy.ops.sculpt.brush_stroke('INVOKE_DEFAULT',
                                              mode='INVERT' if event.ctrl else 'NORMAL',
                                              brush_toggle='SMOOTH' if event.shift else 'None')
        finally:
            if temporary_grab:
                restore_grab_after_smooth(context)
        return {'FINISHED'} if 'RUNNING_MODAL' in result or 'FINISHED' in result else {'CANCELLED'}


class FREECLAY_WST_sculpt(bpy.types.WorkSpaceTool):
    bl_space_type='VIEW_3D'
    bl_context_mode='SCULPT'
    bl_idname='freeclay.sculpt_tool'
    bl_label='粘土を彫る'
    bl_description='選んだブラシで粘土を彫ります'
    bl_icon='brush.sculpt.draw'
    bl_options={'USE_BRUSHES'}
    bl_keymap=(('freeclay.sculpt_stroke',{'type':'LEFTMOUSE','value':'PRESS','any':True},None),)


class FREECLAY_OT_grab(bpy.types.Operator):
    bl_idname='freeclay.grab'
    bl_label='つかんで引っ張る'
    bl_options={'REGISTER','UNDO','BLOCKING'}

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def invoke(self,context,event):
        self.stroke=None
        self.area=context.area;self.region=context.region;self.rv=context.region_data
        if self.region.type!='WINDOW' or self.rv is None:
            return {'CANCELLED'}
        try:
            self.stroke=Stroke(context,build_reducer=False)
            obj=self.stroke.obj
            mouse=Vector((event.mouse_region_x,event.mouse_region_y))
            origin=view3d_utils.region_2d_to_origin_3d(self.region,self.rv,mouse)
            direction=view3d_utils.region_2d_to_vector_3d(self.region,self.rv,mouse)
            inverse=obj.matrix_world.inverted()
            hit,location,_,_=obj.ray_cast(inverse@origin,(inverse.to_3x3()@direction).normalized())
            if not hit:
                self._end(context,True);return {'CANCELLED'}
            vertex=min(obj.data.vertices,key=lambda v:(v.co-location).length_squared)
            self.anchor=obj.matrix_world@vertex.co
            self.start=view3d_utils.region_2d_to_location_3d(self.region,self.rv,mouse,self.anchor)
            s=context.scene.freeclay
            self.strength=s.grab_strength
            if s.grab_pressure and getattr(event,'is_tablet',False):
                self.strength*=max(0,min(1,event.pressure))
            radius=reference_diameter(obj)*s.grab_diameter*.5
            self.deformation=GrabDeformation(obj.data,obj.matrix_world,self.anchor,radius,s.symmetry)
            _active_grabs.add(self)
            context.window_manager.modal_handler_add(self)
            self.area.header_text_set('Grab  ·  カーソルへ引っ張る  ·  Esc: この一筆を戻す')
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._end(context,True);self.report({'ERROR'},str(exc));return {'CANCELLED'}

    def _move(self,event):
        mouse=Vector((event.mouse_x-self.region.x,event.mouse_y-self.region.y))
        point=view3d_utils.region_2d_to_location_3d(self.region,self.rv,mouse,self.anchor)
        delta=point-self.start
        if delta.length_squared>1e-18 or self.stroke.changed:
            self.stroke.changed=True
            self.deformation.apply(delta,self.strength)
            self.area.tag_redraw()

    def modal(self,context,event):
        try:
            if event.type in {'ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'}:
                self._end(context,True);return {'CANCELLED'}
            if event.type in {'MOUSEMOVE','INBETWEEN_MOUSEMOVE'}:
                self._move(event)
            if event.type=='LEFTMOUSE' and event.value=='RELEASE':
                self._move(event);self._end(context,False);return {'FINISHED'}
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._end(context,True);self.report({'ERROR'},str(exc));return {'CANCELLED'}

    def _end(self,context,cancel):
        try:
            if self.stroke:
                self.stroke.finish(context,cancel);self.stroke=None
        finally:
            _active_grabs.discard(self)
            self.area.header_text_set(None);self.area.tag_redraw()

    def cancel(self,context):
        self._end(context,True)


class FREECLAY_WST_grab(bpy.types.WorkSpaceTool):
    bl_space_type='VIEW_3D'
    bl_context_mode='SCULPT'
    bl_idname='freeclay.grab_tool'
    bl_label='つかむ'
    bl_description='大きな範囲を、カーソルへしっかり引っ張る'
    bl_icon='brush.sculpt.grab'
    bl_keymap=(('freeclay.sculpt_stroke',{'type':'LEFTMOUSE','value':'PRESS','any':True},None),)

    @staticmethod
    def draw_settings(context,layout,tool):
        layout.prop(context.scene.freeclay,'grab_strength')
        layout.prop(context.scene.freeclay,'grab_diameter')

    @staticmethod
    def draw_cursor(context,tool,xy):
        if not owned(context) or _active_grabs or _active_strokes:
            return
        from gpu_extras.presets import draw_circle_2d
        region=context.region;rv=context.region_data
        if region is None or rv is None:
            return
        mouse=Vector((xy[0]-region.x,xy[1]-region.y))
        origin=view3d_utils.region_2d_to_origin_3d(region,rv,mouse)
        direction=view3d_utils.region_2d_to_vector_3d(region,rv,mouse)
        obj=context.object;inv=obj.matrix_world.inverted()
        hit,location,_,_=obj.ray_cast(inv@origin,(inv.to_3x3()@direction).normalized())
        depth=obj.matrix_world@location if hit else obj.matrix_world.translation
        radius=reference_diameter(obj)*context.scene.freeclay.grab_diameter*.5
        right=rv.view_rotation@Vector((radius,0,0))
        a=view3d_utils.location_3d_to_region_2d(region,rv,depth)
        b=view3d_utils.location_3d_to_region_2d(region,rv,depth+right)
        if a is not None and b is not None:
            draw_circle_2d(xy,(1,.65,.25,.85),(b-a).length,segments=96)


class FREECLAY_PT_panel(bpy.types.Panel):
    bl_label='FreeClay'
    bl_idname='FREECLAY_PT_panel'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='FreeClay'

    def draw(self,context):
        layout=self.layout
        if not owned(context):
            layout.label(text='触って、形をつくる。')
            layout.operator('freeclay.open_clay',text='新しい粘土',icon='MESH_UVSPHERE').new_sphere=True
            layout.operator('freeclay.open_clay',icon='DUPLICATE')
            layout.prop(context.scene.freeclay,'auto_detail')
            return
        col=layout.column(align=True)
        for kind,label in [('DRAW','盛る  ·  D'),('GRAB','つかむ  ·  G'),('SMOOTH','ならす  ·  S'),
                           ('CREASE','溝  ·  C'),('REDUCE','軽くする  ·  Q')]:
            col.operator('freeclay.brush',text=label).kind=kind
        layout.separator()
        s=context.scene.freeclay
        box=layout.box()
        box.label(text='Grab · つかむ')
        box.prop(s,'grab_strength');box.prop(s,'grab_diameter');box.prop(s,'grab_pressure')
        layout.label(text='Reduce · 軽くする')
        layout.prop(s,'radius'); layout.prop(s,'strength'); layout.prop(s,'preserve')
        layout.prop(s,'pressure'); layout.prop(s,'symmetry')
        if s.symmetry:
            layout.operator('freeclay.symmetrize')
        layout.label(text='Rを押して一筆だけ軽くする')
        layout.label(text='Space: 手元のブラシメニュー')
        layout.label(text='右ドラッグ: 物体中心で回転')
        layout.label(text='中ドラッグ: 触った位置で回転')
        layout.label(text='Shift + 右 / 中: 平行移動')
        layout.label(text='Ctrl Z: 戻す / Esc: 一筆を取消')
        layout.operator('freeclay.leave')


class FREECLAY_MT_brushes(bpy.types.Menu):
    bl_label='FreeClay'
    bl_idname='FREECLAY_MT_brushes'

    def draw(self,context):
        pie=self.layout.menu_pie()
        for kind,label in [('GRAB','つかむ'),('REDUCE','軽くする'),('SMOOTH','ならす'),
                           ('DRAW','盛る'),('CREASE','溝')]:
            pie.operator('freeclay.brush',text=label).kind=kind


class FREECLAY_OT_menu(bpy.types.Operator):
    bl_idname='freeclay.menu'
    bl_label='手元のブラシメニュー'

    @classmethod
    def poll(cls,context):
        return sculpt_context(context)

    def execute(self,context):
        bpy.ops.wm.call_menu_pie(name='FREECLAY_MT_brushes')
        return {'FINISHED'}


_classes=(FreeClaySettings,FREECLAY_OT_brush,FREECLAY_OT_clay,FREECLAY_OT_leave,
          FREECLAY_OT_reduce,FREECLAY_OT_navigate,FREECLAY_PT_panel,FREECLAY_MT_brushes,FREECLAY_OT_menu,
          FREECLAY_OT_sculpt_stroke)
_classes=_classes+(FREECLAY_OT_symmetrize,FREECLAY_OT_grab)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.freeclay=PointerProperty(type=FreeClaySettings)
    bpy.utils.register_tool(FREECLAY_WST_reduce,separator=True,group=False)
    bpy.utils.register_tool(FREECLAY_WST_sculpt,after={'freeclay.reduce_tool'},group=False)
    bpy.utils.register_tool(FREECLAY_WST_grab,after={'freeclay.sculpt_tool'},group=False)
    config=bpy.context.window_manager.keyconfigs.addon
    if config:
        km=config.keymaps.new(name='Sculpt',space_type='EMPTY')
        for key,kind in [('D','DRAW'),('G','GRAB'),('S','SMOOTH'),('C','CREASE'),('Q','REDUCE')]:
            kmi=km.keymap_items.new('freeclay.brush',key,'PRESS')
            kmi.properties.kind=kind
            _keymaps.append((km,kmi))
        kmi=km.keymap_items.new('freeclay.reduce','R','PRESS')
        kmi.properties.temporary=True
        _keymaps.append((km,kmi))
        _keymaps.append((km,km.keymap_items.new('freeclay.menu','SPACE','PRESS')))
        for button in ('RIGHTMOUSE','MIDDLEMOUSE'):
            for shift in (False,True):
                kmi=km.keymap_items.new('freeclay.navigate',button,'PRESS',shift=shift)
                kmi.properties.pan=shift
                kmi.properties.cursor_pivot=button=='MIDDLEMOUSE'
                _keymaps.append((km,kmi))


def unregister():
    for callback in list(_grab_restore_timers):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    _grab_restore_timers.clear()
    for op in list(_active_grabs):
        op._end(bpy.context,True)
    for op in list(_active_strokes):
        op._end(bpy.context,True)
    for km,kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.utils.unregister_tool(FREECLAY_WST_reduce)
    bpy.utils.unregister_tool(FREECLAY_WST_sculpt)
    bpy.utils.unregister_tool(FREECLAY_WST_grab)
    del bpy.types.Scene.freeclay
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
