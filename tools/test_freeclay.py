"""Run with Blender --background --factory-startup; only synthetic owned data."""
import sys, pathlib, json, time, math, traceback
import bpy, bmesh
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import freeclay
from freeclay.reducer import Reducer

report={'version':bpy.app.version_string,'tests':[]}


def make_sphere(subdivisions=6, deform=False):
    mesh=bpy.data.meshes.new('TestClay')
    bm=bmesh.new(); bmesh.ops.create_icosphere(bm,subdivisions=subdivisions,radius=1)
    bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),direction='-X',dist=1e-6)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    if deform:
        for v in bm.verts:
            x,y,z=v.co
            v.co *= 1 + 0.12*math.exp(-((x-.13)**2+(y-.08)**2)/.012) * max(0,z)
    bm.to_mesh(mesh); bm.free()
    return mesh


def surface(mesh):
    bm=bmesh.new(); bm.from_mesh(mesh); tree=BVHTree.FromBMesh(bm); bm.free()
    return tree


def samples(mesh):
    yield from (v.co.copy() for v in mesh.vertices)
    for f in mesh.polygons:
        p=[mesh.vertices[i].co for i in f.vertices]
        yield sum(p,Vector())/len(p)
        for a,b in zip(p,p[1:]+p[:1]):
            yield (a+b)*.5


def geometry_stats(mesh):
    bm=bmesh.new(); bm.from_mesh(mesh)
    stats={'manifold':all(e.is_manifold for e in bm.edges),
           'faces':len(bm.faces),'vertices':len(bm.verts),
           'degenerate':sum(f.calc_area()<1e-14 for f in bm.faces),
           'volume':bm.calc_volume(signed=True)}
    bm.free(); return stats


def test_reduction():
    mesh=make_sphere(deform=True)
    original=mesh.copy(); ref=surface(original)
    before=geometry_stats(mesh)
    reducer=Reducer(mesh)
    results=[]
    start=time.perf_counter()
    for i in range(30):
        x=.18*math.sin(i*.7)
        center=Vector((x,0,math.sqrt(1-x*x)))
        results.append(reducer.dab(center,.55,(0,0,-1),.8,.8,max_collapses=96,budget_ms=28))
    reducer.write_mesh(mesh); reducer.close()
    after=geometry_stats(mesh)
    reduced_ref=surface(mesh)
    forward=max(ref.find_nearest(p)[3] for p in samples(mesh))
    reverse=max(reduced_ref.find_nearest(p)[3] for p in samples(original))
    untouched={tuple(v.co) for v in original.vertices if v.co.z<.75}
    surviving={tuple(v.co) for v in mesh.vertices}
    assert untouched <= surviving,'outside brush changed'
    assert after['faces']<before['faces']-50,(before,after,results)
    assert after['manifold'] and not after['degenerate'],after
    assert after['volume']>0
    assert forward<.003 and reverse<.003,(forward,reverse)
    timing=sorted(r['milliseconds'] for r in results)
    report['tests'].append({'name':'local reduction, ridge, shape error and manifold','passed':True,
       'before':before,'after':after,'sampled_error_forward':forward,'sampled_error_reverse':reverse,
       'dab_ms_median':timing[len(timing)//2],'dab_ms_max':max(timing),
       'total_seconds':time.perf_counter()-start,'dabs':results})
    return original,mesh


def test_mask_and_back_shell():
    mesh=make_sphere(5)
    initial_vertices=len(mesh.vertices)
    mask=mesh.attributes.new('.sculpt_mask','FLOAT','POINT')
    locked=set()
    for v,d in zip(mesh.vertices,mask.data):
        if v.co.x>0:
            d.value=1
            locked.add(tuple(v.co))
    back={tuple(v.co) for v in mesh.vertices if v.co.z<0}
    reducer=Reducer(mesh)
    for i in range(8):
        reducer.dab((0,0,1),1.8,(0,0,-1),1,.6,max_collapses=100,budget_ms=60)
    reducer.write_mesh(mesh); reducer.close()
    surviving={tuple(v.co) for v in mesh.vertices}
    assert locked<=surviving and back<=surviving
    assert len(mesh.vertices)<initial_vertices
    report['tests'].append({'name':'mask and back surface preserved','passed':True})


def test_scale_cancel_and_session():
    freeclay.register()
    window=bpy.context.window_manager.windows[0]
    area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
    region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(window=window,area=area,region=region):
        space=area.spaces.active
        space.shading.type='WIREFRAME'
        space.overlay.show_overlays=False
        space.overlay.wireframe_opacity=.7
        display_names=('show_stats','show_overlays','show_wireframes','wireframe_threshold','wireframe_opacity')
        display_before={name:getattr(space.overlay,name) for name in display_names}
        result=bpy.ops.freeclay.open_clay(new_sphere=True)
        assert result=={'FINISHED'},result
        obj=bpy.context.object
        assert obj.mode=='SCULPT' and obj.get('freeclay_enabled')
        assert space.shading.type=='SOLID' and space.overlay.show_stats and space.overlay.show_overlays
        # Emulate upgrading a saved 0.1.2 session, which lacks the new keys.
        saved=json.loads(obj['freeclay_view_before'])
        saved.pop('overlay.wireframe_opacity')
        obj['freeclay_view_before']=json.dumps(saved)
        bpy.ops.freeclay.display(kind='WIRE')
        assert space.overlay.show_wireframes and abs(space.overlay.wireframe_opacity-.35)<1e-6
        bpy.ops.freeclay.display(kind='WIRE')
        assert not space.overlay.show_wireframes
        bpy.ops.freeclay.display(kind='STATS')
        assert not space.overlay.show_stats
        bpy.ops.freeclay.display(kind='STATS')
        assert space.overlay.show_stats
        for kind in ('GRAB','SMOOTH','CREASE','DRAW','REDUCE'):
            assert bpy.ops.freeclay.brush(kind=kind)=={'FINISHED'}
        obj.scale=(1.2,.8,1.5)
        bpy.context.view_layer.update()
        original=[tuple(v.co) for v in obj.data.vertices]
        faces=len(obj.data.polygons)
        from freeclay import Stroke
        stroke=Stroke(bpy.context)
        for _ in range(5):
            stroke.dab(Vector((0,0,2.25)),.9,Vector((0,0,-1)),1,.5)
        assert len(obj.data.polygons)<faces
        stroke.finish(bpy.context,cancel=True)
        assert obj.mode=='SCULPT'
        assert [tuple(v.co) for v in obj.data.vertices]==original
        assert len(obj.data.polygons)==faces
        stroke=Stroke(bpy.context)
        for _ in range(5):
            stroke.dab(Vector((0,0,2.25)),.9,Vector((0,0,-1)),1,.5)
        stroke.finish(bpy.context)
        assert obj.mode=='SCULPT' and len(obj.data.polygons)<faces
        # Save only this owned factory process, never the user's editing session.
        bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'verification'/'test-session.blend'))
        assert bpy.ops.freeclay.leave()=={'FINISHED'}
        assert space.shading.type=='WIREFRAME'
        assert {name:getattr(space.overlay,name) for name in display_names}==display_before
        report['tests'].append({'name':'wire and live stats toggles, old session compatibility, display restoration','passed':True})
        bpy.ops.mesh.primitive_cube_add()
        source=bpy.context.object
        source.name='Untouched Source'
        modifier=source.modifiers.new('Test Bevel','BEVEL'); modifier.width=.1; modifier.segments=2
        source_geometry=[tuple(v.co) for v in source.data.vertices]
        assert bpy.ops.freeclay.open_clay(new_sphere=False)=={'FINISHED'}
        copied=bpy.context.object
        assert copied!=source and copied.data!=source.data and source.hide_get()
        assert [tuple(v.co) for v in source.data.vertices]==source_geometry
        assert len(source.modifiers)==1 and not copied.modifiers
        assert bpy.ops.freeclay.leave()=={'FINISHED'}
        assert bpy.context.object==source and not source.hide_get() and copied.hide_get()
    freeclay.unregister()
    freeclay.register(); freeclay.unregister()
    report['tests'].append({'name':'register, assets, scale, cancel, save, source copy protection, leave, cleanup',
                            'passed':True})


def test_thin_shell_and_edges():
    mesh=bpy.data.meshes.new('DoubleSheet')
    bm=bmesh.new()
    lower=set()
    for z in (0,-.004):
        created=bmesh.ops.create_grid(bm,x_segments=35,y_segments=35,size=1)['verts']
        for v in created:
            v.co.z=z
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    bm.to_mesh(mesh); bm.free()
    lower={tuple(v.co) for v in mesh.vertices if v.co.z<-.002}
    boundary={tuple(v.co) for v in mesh.vertices if max(abs(v.co.x),abs(v.co.y))>.99}
    count=len(mesh.polygons)
    reducer=Reducer(mesh)
    for i in range(25):
        reducer.dab((.08*math.sin(i),0,0),.8,(0,0,-1),1,.8,max_collapses=100,budget_ms=40)
    reducer.write_mesh(mesh); reducer.close()
    surviving={tuple(v.co) for v in mesh.vertices}
    assert lower<=surviving,'thin rear shell was touched'
    assert boundary<=surviving,'boundary changed'
    assert len(mesh.polygons)<count
    report['tests'].append({'name':'close parallel shells, boundary and planar reduction','passed':True,
                            'before_faces':count,'after_faces':len(mesh.polygons)})


def test_mirrored_reduce():
    mesh=make_sphere(6)
    before=len(mesh.polygons)
    reducer=Reducer(mesh)
    matched=sum(reducer._mirror_vertex(v) is not None for v in reducer.vertices)
    timings=[]
    for i in range(20):
        r=reducer.dab((.35,.03*math.sin(i),.93),.55,(0,0,-1),1,.7,
                    max_collapses=96,budget_ms=40,mirror_x=True)
        timings.append(r['milliseconds'])
    reducer.write_mesh(mesh); reducer.close()
    from mathutils.kdtree import KDTree
    tree=KDTree(len(mesh.vertices))
    for v in mesh.vertices:
        tree.insert(v.co,v.index)
    tree.balance()
    error=max(tree.find(Vector((-v.co.x,v.co.y,v.co.z)))[2] for v in mesh.vertices)
    assert len(mesh.polygons)<before-50,(before,len(mesh.polygons),matched,error)
    assert error<1e-6,error
    assert geometry_stats(mesh)['manifold']
    report['tests'].append({'name':'paired X-symmetric reduction','passed':True,
                            'faces_before':before,'faces_after':len(mesh.polygons),'max_mirror_error':error,
                            'dab_ms_median':sorted(timings)[len(timings)//2],'dab_ms_max':max(timings)})


def test_grab_deformation():
    from freeclay.grab import GrabDeformation
    mesh=make_sphere(5)
    matrix=Matrix.Rotation(.4,4,'Z')@Matrix.Diagonal((1.2,.8,1.5,1))
    vertex=min(mesh.vertices,key=lambda v:(v.co-Vector((.5,0,.86))).length_squared)
    index=vertex.index
    original=[v.co.copy() for v in mesh.vertices]
    anchor=matrix@vertex.co
    delta=matrix.to_3x3()@Vector((.3,.1,.05))
    grab=GrabDeformation(mesh,matrix,anchor,15,True)
    grab.apply(delta,1)
    assert (matrix@mesh.vertices[index].co-anchor-delta).length<1e-6
    from mathutils.kdtree import KDTree
    tree=KDTree(len(mesh.vertices))
    for v in mesh.vertices:tree.insert(v.co,v.index)
    tree.balance()
    error=max(tree.find(Vector((-v.co.x,v.co.y,v.co.z)))[2] for v in mesh.vertices)
    assert error<1e-6,error
    grab.apply(delta,.25)
    assert (matrix@mesh.vertices[index].co-anchor-delta*.25).length<1e-6
    grab.apply(Vector((0,0,0)),1)
    assert all((v.co-p).length<1e-7 for v,p in zip(mesh.vertices,original))
    mask=mesh.attributes.new('.sculpt_mask','FLOAT','POINT')
    mask.data[index].value=1
    grab=GrabDeformation(mesh,matrix,anchor,15,False)
    grab.apply(delta,1)
    assert (mesh.vertices[index].co-original[index]).length<1e-7
    report['tests'].append({'name':'Grab full tracking, strength, symmetry, transforms, absolute updates and mask',
                            'passed':True,'max_mirror_error':error})


try:
    original,reduced=test_reduction()
    test_mask_and_back_shell()
    test_thin_shell_and_edges()
    test_mirrored_reduce()
    test_grab_deformation()
    test_scale_cancel_and_session()
    report['passed']=True
except Exception:
    report['passed']=False
    report['error']=traceback.format_exc()
    raise
finally:
    (ROOT/'verification'/'tests.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True))
