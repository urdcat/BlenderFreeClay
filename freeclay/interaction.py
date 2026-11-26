"""Grab radius in screen/model units and a preview retained while editing UI."""
import bpy, math, gpu
from mathutils import Vector
from bpy_extras import view3d_utils as view
from gpu_extras.presets import draw_circle_2d
from gpu_extras.batch import batch_for_shader
from .grab import mirror_brush_joined

_anchors={}
_handler=None
adjusting=set()


def redraw(self=None,context=None):
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':area.tag_redraw()


def sync_size_mode(self,context):
    mode='VIEW' if self.size_mode=='SCREEN' else 'SCENE'
    for brush in bpy.data.brushes:
        if brush.name.startswith('FreeClay '):brush.use_locked_size=mode
    redraw()


def active_kind(context):
    tool=context.workspace.tools.from_space_view3d_mode('SCULPT')
    if tool and tool.idname=='freeclay.reduce_tool':return 'REDUCE'
    if tool and tool.idname=='freeclay.grab_tool':return 'GRAB'
    if tool and tool.idname=='freeclay.transform_tool':return context.scene.freeclay.transform_kind
    brush=context.scene.tool_settings.sculpt.brush
    return brush.sculpt_brush_type if brush else 'GRAB'


def size_binding(context,kind=None):
    s=context.scene.freeclay;kind=kind or active_kind(context)
    screen=s.size_mode=='SCREEN'
    if kind in {'GRAB','ROTATE','SCALE'}:return s,'grab_radius_px' if screen else 'grab_diameter'
    if kind=='REDUCE':return s,'radius' if screen else 'reduce_diameter'
    return context.scene.tool_settings.sculpt.brush,'size' if screen else 'unprojected_size'


def radius_world(context,anchor,kind=None):
    s=context.scene.freeclay
    kind=kind or active_kind(context)
    owner,field=size_binding(context,kind)
    value=getattr(owner,field)
    # Blender 5.2 stores native brush sizes as diameters, unlike our radii.
    if kind not in {'GRAB','REDUCE','ROTATE','SCALE'}:value*=.5
    if s.size_mode=='MODEL':
        if kind not in {'GRAB','REDUCE','ROTATE','SCALE'}:return value
        obj=context.object
        diameter=obj.get('freeclay_reference_diameter',max(obj.dimensions))
        return max(1e-8,diameter*value*.5)
    region=context.region;rv=context.region_data
    point=view.location_3d_to_region_2d(region,rv,anchor)
    if point is None:return 1e-8
    a=view.region_2d_to_location_3d(region,rv,point,anchor)
    b=view.region_2d_to_location_3d(region,rv,point+Vector((value,0)),anchor)
    return max(1e-8,(b-a).length)


def remember(context,anchor):
    _anchors[context.area.as_pointer()]=(context.object.as_pointer(),Vector(anchor))


def anchor_at(context,mouse):
    obj=context.object;rv=context.region_data;region=context.region
    inv=obj.matrix_world.inverted()
    origin=view.region_2d_to_origin_3d(region,rv,mouse)
    direction=view.region_2d_to_vector_3d(region,rv,mouse)
    hit,p,_,_=obj.ray_cast(inv@origin,(inv.to_3x3()@direction).normalized())
    if hit:return obj.matrix_world@p
    return last_anchor(context)


def last_anchor(context):
    item=_anchors.get(context.area.as_pointer())
    if item and item[0]==context.object.as_pointer():return item[1].copy()
    return context.object.matrix_world@(sum((Vector(p) for p in context.object.bound_box),Vector())/8)


def capture_cursor(context,xy):
    if context.area.as_pointer() in adjusting:return
    mouse=Vector((xy[0]-context.region.x,xy[1]-context.region.y))
    if 0<=mouse.x<context.region.width and 0<=mouse.y<context.region.height:
        anchor=anchor_at(context,mouse)
        previous=last_anchor(context)
        remember(context,anchor)
        if (anchor-previous).length>1e-6:context.area.tag_redraw()


def circles(context,kind=None):
    anchor=last_anchor(context);radius=radius_world(context,anchor,kind)
    centers=[anchor]
    if context.scene.freeclay.symmetry:
        local=context.object.matrix_world.inverted()@anchor
        if abs(local.x)>1e-6:
            local.x=-local.x;centers.append(context.object.matrix_world@local)
    result=[]
    for center in centers:
        a=view.location_3d_to_region_2d(context.region,context.region_data,center)
        b=view.location_3d_to_region_2d(context.region,context.region_data,center+context.region_data.view_rotation@Vector((radius,0,0)))
        if a is not None and b is not None:result.append((a,(b-a).length))
    return result


def joined_outline(context,kind=None):
    kind=kind or active_kind(context)
    if kind not in {'GRAB','REDUCE'} or not context.scene.freeclay.symmetry:return None
    anchor=last_anchor(context);matrix=context.object.matrix_world
    if not mirror_brush_joined(matrix,matrix.inverted()@anchor,radius_world(context,anchor,kind)):return None
    disks=circles(context,kind)
    if len(disks)<2:return None
    # Convex hull of the two projected disks: one continuous capsule contour,
    # including the different apparent radii in perspective views.
    points=sorted(set((center.x+radius*math.cos(i*math.tau/96),center.y+radius*math.sin(i*math.tau/96))
                      for center,radius in disks for i in range(96)))
    def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    def half(points):
        hull=[]
        for p in points:
            while len(hull)>1 and cross(hull[-2],hull[-1],p)<=0:hull.pop()
            hull.append(p)
        return hull
    points=half(points)[:-1]+half(reversed(points))[:-1]
    return points+[points[0]] if points else None


def draw_joined(context,kind,color):
    points=joined_outline(context,kind)
    if points is None:return False
    shader=gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind();shader.uniform_float('color',color)
    batch_for_shader(shader,'LINE_STRIP',{'pos':points}).draw(shader)
    return True


def draw_preview():
    context=bpy.context
    if not context.area or context.area.type!='VIEW_3D' or not context.region or context.region.type!='WINDOW':return
    obj=context.object
    if not obj or obj.mode!='SCULPT' or not obj.get('freeclay_enabled'):return
    tool=context.workspace.tools.from_space_view3d_mode('SCULPT')
    if not tool or not tool.idname.startswith('freeclay.'):return
    if context.scene.freeclay.transform_target=='OBJECT' and active_kind(context) in {'GRAB','ROTATE','SCALE'}:return
    gpu.state.blend_set('ALPHA')
    try:
        if draw_joined(context,active_kind(context),(1,.65,.22,.95)):return
        for i,(center,radius) in enumerate(circles(context)):
            color=(1,.65,.22,.95 if i==0 else .5)
            draw_circle_2d(center,color,radius,segments=96)
            draw_circle_2d(center,(1,.65,.22,.25),radius*.5,segments=64)
    finally:gpu.state.blend_set('NONE')


class FREECLAY_OT_brush_size(bpy.types.Operator):
    bl_idname='freeclay.brush_size'
    bl_label='範囲を見ながら調整'
    bl_description='F: 左右に動かして範囲を調整。左クリックかEnterで確定、Escで取消'
    bl_options={'BLOCKING'}

    @classmethod
    def poll(cls,context):
        return (context.area and context.area.type=='VIEW_3D' and context.object and context.object.mode=='SCULPT' and context.object.get('freeclay_enabled')
                and not (active_kind(context) in {'GRAB','ROTATE','SCALE'} and context.scene.freeclay.transform_target=='OBJECT'))

    def invoke(self,context,event):
        self.area=context.area
        self.scene=context.scene
        self.owner,self.field=size_binding(context)
        self.original=getattr(self.owner,self.field)
        self.base=self.original
        self.start_x=event.mouse_x
        if context.region.type=='WINDOW':remember(context,anchor_at(context,Vector((event.mouse_region_x,event.mouse_region_y))))
        adjusting.add(self.area.as_pointer())
        self.area.header_text_set('範囲: 左右に動かす / ホイール  ·  左クリック: 確定  ·  Esc: 取消')
        context.window_manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def finish(self,cancel=False):
        if cancel:setattr(self.owner,self.field,self.original)
        adjusting.discard(self.area.as_pointer())
        self.area.header_text_set(None);self.area.tag_redraw()

    def modal(self,context,event):
        if event.type in {'ESC','RIGHTMOUSE','WINDOW_DEACTIVATE'}:
            self.finish(True);return {'CANCELLED'}
        if event.type in {'LEFTMOUSE','RET','NUMPAD_ENTER'} and event.value=='PRESS':
            self.finish();return {'FINISHED'}
        if event.type=='MOUSEMOVE':
            value=self.base*math.exp(max(-8,min(8,(event.mouse_x-self.start_x)/180)))
            setattr(self.owner,self.field,round(value) if self.owner.bl_rna.properties[self.field].type=='INT' else value)
        if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'}:
            self.base=getattr(self.owner,self.field)*(1.1 if event.type=='WHEELUPMOUSE' else 1/1.1)
            self.start_x=event.mouse_x
            setattr(self.owner,self.field,round(self.base) if self.owner.bl_rna.properties[self.field].type=='INT' else self.base)
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def cancel(self,context):self.finish(True)


def register():
    global _handler
    bpy.utils.register_class(FREECLAY_OT_brush_size)
    _handler=bpy.types.SpaceView3D.draw_handler_add(draw_preview,(),'WINDOW','POST_PIXEL')


def unregister():
    global _handler
    if _handler is not None:bpy.types.SpaceView3D.draw_handler_remove(_handler,'WINDOW')
    _handler=None;_anchors.clear();adjusting.clear()
    bpy.utils.unregister_class(FREECLAY_OT_brush_size)
