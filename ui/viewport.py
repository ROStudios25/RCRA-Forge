"""
ui/viewport.py
3D OpenGL viewport widget for RCRA Forge.

Uses PyQt6 QOpenGLWidget with a minimal forward-compatible core profile
shader pipeline. Renders mesh sub-meshes with per-material shading and
supports arcball camera navigation.
"""

import math
import numpy as np
from typing import Optional

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QMouseEvent, QWheelEvent

from ui.controls_dialog import load_controls

try:
    from OpenGL.GL import *
    from OpenGL.GL.shaders import compileShader, compileProgram
    _HAS_OPENGL = True
except ImportError:
    _HAS_OPENGL = False

from core.mesh import ModelAsset, MeshDefinition, mesh_to_numpy


# ── GLSL Shaders ──────────────────────────────────────────────────────────────

VERT_SRC = """
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUV;

uniform mat4 uMVP;
uniform mat4 uModel;
uniform mat3 uNormal;

out vec3 vNormal;
out vec3 vWorldPos;
out vec2 vUV;

void main() {
    vec4 worldPos = uModel * vec4(aPos, 1.0);
    vWorldPos  = worldPos.xyz;
    vNormal    = normalize(uNormal * aNormal);
    vUV        = aUV;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

FRAG_SRC = """
#version 330 core
in vec3 vNormal;
in vec3 vWorldPos;
in vec2 vUV;

uniform vec3      uLightDir;
uniform vec3      uFillDir;
uniform vec3      uBaseColor;
uniform bool      uWireframe;
uniform bool      uHasTexture;
uniform bool      uHasNormal;
uniform sampler2D uAlbedo;
uniform sampler2D uNormalMap;

out vec4 FragColor;

// Simple normal perturbation — offsets vertex normal by normal map XY.
// More stable than full cotangent TBN at large world scales.
vec3 perturb_normal(vec3 N, vec2 uv, sampler2D nmap) {
    vec3 nm = texture(nmap, uv).rgb * 2.0 - 1.0;
    // BC5 RG normal map — reconstruct Z
    nm.z = sqrt(max(0.001, 1.0 - dot(nm.xy, nm.xy)));
    // Build a simple local frame from vertex normal
    vec3 up    = abs(N.z) < 0.999 ? vec3(0,0,1) : vec3(1,0,0);
    vec3 T     = normalize(cross(up, N));
    vec3 B     = cross(N, T);
    // Blend: use nm.xy to perturb N, keep Z influence from vertex normal
    vec3 perturbed = normalize(T * nm.x + B * nm.y + N * (nm.z + 0.5));
    return perturbed;
}

void main() {
    if (uWireframe) {
        FragColor = vec4(0.2, 0.8, 1.0, 1.0);
        return;
    }

    vec3 n = normalize(vNormal);

    if (uHasNormal) {
        n = perturb_normal(n, vUV, uNormalMap);
    }

    // Two-sided lighting
    float NdL  = abs(dot(n, normalize(uLightDir)));
    float NdL2 = abs(dot(n, normalize(uFillDir)));
    float light = NdL * 0.8 + NdL2 * 0.3 + 0.35;

    vec3 col;
    if (uHasTexture) {
        vec3 tex = texture(uAlbedo, vUV).rgb;
        col = tex * light;
    } else {
        col = uBaseColor * light;
    }
    FragColor = vec4(col, 1.0);
}
"""

GRID_VERT = """
#version 330 core
layout(location=0) in vec3 aPos;

uniform mat4 uInvVP;

out vec3 vNear;
out vec3 vFar;

vec3 unproject(float x, float y, float z, mat4 invVP) {
    vec4 v = invVP * vec4(x, y, z, 1.0);
    return v.xyz / v.w;
}

void main() {
    gl_Position = vec4(aPos, 1.0);
    vNear = unproject(aPos.x, aPos.y, -1.0, uInvVP);
    vFar  = unproject(aPos.x, aPos.y,  1.0, uInvVP);
}
"""

GRID_FRAG = """
#version 330 core
in vec3 vNear;
in vec3 vFar;

uniform float uGridY;
uniform bool  uOrtho;
uniform vec3  uEye;       // camera world-space eye position

out vec4 FragColor;

float gridLine(vec2 uv, float scale) {
    vec2 g = abs(fract(uv / scale - 0.5) - 0.5) / fwidth(uv / scale);
    return min(g.x, g.y);
}

void main() {
    vec3 rayOrigin, rayDir;

    if (uOrtho) {
        // In ortho all rays are parallel. The direction is the same for every
        // fragment: from near to far (both already in world space).
        // The origin per-fragment is vNear — it lies on the near plane in
        // world space at the correct XZ for this screen pixel.
        rayDir    = normalize(vFar - vNear);
        rayOrigin = vNear;
    } else {
        // Perspective: rays diverge from the eye point.
        rayOrigin = uEye;
        rayDir    = normalize(vFar - uEye);
    }

    float dY = rayDir.y;
    if (abs(dY) < 1e-5) discard;

    float t = (uGridY - rayOrigin.y) / dY;
    if (t < 0.0) discard;

    vec3  pos     = rayOrigin + t * rayDir;
    float dist    = length(pos.xz - uEye.xz);

    float g1   = gridLine(pos.xz, 1.0);
    float g2   = gridLine(pos.xz, 0.1);
    float line = min(g1, g2);

    float fadeRange = uOrtho ? 200.0 : 80.0;
    float alpha = (1.0 - min(line, 1.0)) * (1.0 - smoothstep(0.0, fadeRange, dist));
    if (alpha < 0.01) discard;

    vec3 col = (g2 > g1) ? vec3(0.6, 0.65, 0.75) : vec3(0.5, 0.55, 0.65);
    FragColor = vec4(col, alpha * 0.85);
}
"""


# ── Arcball Camera ─────────────────────────────────────────────────────────────

class ArcballCamera:
    def __init__(self):
        self.yaw:    float  = 30.0
        self.pitch:  float  = 25.0
        self.dist:   float  = 5.0
        self.target: np.ndarray = np.zeros(3, dtype=np.float32)

    def view_matrix(self) -> np.ndarray:
        y = math.radians(self.yaw)
        p = math.radians(self.pitch)
        cx, cy, cz = math.cos(y), math.sin(p), math.sin(y)
        eye = self.target + np.array([
            cx * math.cos(p),
            cy,
            cz * math.cos(p),
        ], dtype=np.float32) * self.dist
        return _look_at(eye, self.target, np.array([0, 1, 0], np.float32))

    def eye_position(self) -> np.ndarray:
        y = math.radians(self.yaw)
        p = math.radians(self.pitch)
        return self.target + np.array([
            math.cos(y) * math.cos(p),
            math.sin(p),
            math.sin(y) * math.cos(p),
        ], dtype=np.float32) * self.dist

    def orbit(self, dx: float, dy: float):
        self.yaw   += dx * 0.4
        self.pitch  = max(-89, min(89, self.pitch - dy * 0.4))

    def pan(self, dx: float, dy: float):
        right = np.array([math.cos(math.radians(self.yaw)), 0,
                          -math.sin(math.radians(self.yaw))], np.float32)
        up    = np.array([0, 1, 0], np.float32)
        speed = self.dist * 0.001
        self.target += (-right * dx + up * dy) * speed

    def zoom(self, delta: float):
        # Scale zoom speed by current distance so large models zoom at reasonable speed
        factor = 1.0 - delta * 0.15
        self.dist = max(0.01, self.dist * factor)

    def frame_aabb(self, mn: np.ndarray, mx: np.ndarray):
        self.target = (mn + mx) * 0.5
        diag = np.linalg.norm(mx - mn)
        self.dist = float(diag) * 1.2 if diag > 0 else 5.0


# ── GPU Mesh ──────────────────────────────────────────────────────────────────

class GpuSubMesh:
    def __init__(self):
        self.vao: int = 0
        self.vbo: int = 0
        self.ebo: int = 0
        self.index_count: int = 0
        self.index_type: int = 0   # GL_UNSIGNED_SHORT or GL_UNSIGNED_INT
        self.color: tuple = (0.75, 0.75, 0.75)
        self.texture_id:    int = 0   # OpenGL texture object, 0 = no texture
        self.normal_tex_id: int = 0   # OpenGL normal map texture, 0 = none
        self.material_index: int = -1  # model material index for texture lookup
        self.is_fur: bool = False     # fur/composite shell mesh — can be toggled

    def upload(self, positions: np.ndarray, normals: np.ndarray,
               uvs: np.ndarray, indices: np.ndarray):
        """Upload pre-extracted numpy arrays to the GPU."""
        n = len(positions)
        if n == 0 or indices is None or len(indices) == 0:
            return

        nrm = normals if normals is not None and len(normals) == n \
              else np.zeros((n, 3), np.float32)
        uv  = uvs if uvs is not None and len(uvs) == n \
              else np.zeros((n, 2), np.float32)

        interleaved = np.concatenate([
            positions.astype(np.float32),
            nrm.astype(np.float32),
            uv.astype(np.float32),
        ], axis=1).astype(np.float32)

        self.vao = glGenVertexArrays(1)
        self.vbo = glGenBuffers(1)
        self.ebo = glGenBuffers(1)

        glBindVertexArray(self.vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
        glBufferData(GL_ARRAY_BUFFER, interleaved.nbytes, interleaved, GL_STATIC_DRAW)

        stride = 8 * 4
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(12))
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(2, 2, GL_FLOAT, GL_FALSE, stride, ctypes.c_void_p(24))
        glEnableVertexAttribArray(2)

        if indices.max() < 65536:
            idx = indices.astype(np.uint16)
            self.index_type = GL_UNSIGNED_SHORT
        else:
            idx = indices.astype(np.uint32)
            self.index_type = GL_UNSIGNED_INT

        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.ebo)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL_STATIC_DRAW)
        self.index_count = len(idx)
        glBindVertexArray(0)

    def draw(self):
        if self.vao and self.index_count:
            glBindVertexArray(self.vao)
            glDrawElements(GL_TRIANGLES, self.index_count, self.index_type, None)
            glBindVertexArray(0)

    def free(self):
        if self.vao:
            glDeleteVertexArrays(1, [self.vao])
            glDeleteBuffers(1, [self.vbo])
            glDeleteBuffers(1, [self.ebo])
            self.vao = 0
        if self.texture_id:
            glDeleteTextures(1, [self.texture_id])
            self.texture_id = 0
        if self.normal_tex_id:
            glDeleteTextures(1, [self.normal_tex_id])
            self.normal_tex_id = 0


# ── Viewport Widget ───────────────────────────────────────────────────────────

import ctypes

class Viewport3D(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera     = ArcballCamera()
        self._gpu_meshes: list[GpuSubMesh] = []
        self._shader_prog: int = 0
        self._grid_prog:   int = 0
        self._grid_vao:    int = 0
        self._grid_vbo:    int = 0
        self._grid_count:  int = 0
        self._last_pos:    Optional[QPoint] = None
        self._mouse_mode:  str = 'orbit'
        self._dragging:    bool = False
        self._wireframe:   bool = False
        self._show_fur:    bool = True    # toggle fur/composite shell meshes
        self._pending_model  = None
        self._redraw_pending = False
        self._grid_y         = 0.0
        self._grid_fade_r    = 2.0
        self._cached_material_textures: dict = {}   # persists across LOD switches
        # Load persisted control settings
        self._controls: dict = load_controls()

        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)

    def _redraw(self):
        """Request a repaint through Qt's paint system."""
        self.update()

    def _start_render_loop(self):
        if not hasattr(self, '_render_timer'):
            from PyQt6.QtCore import QTimer
            self._render_timer = QTimer(self)
            self._render_timer.timeout.connect(self.update)
        if not self._render_timer.isActive():
            self._render_timer.start(16)  # 60fps

    def _stop_render_loop(self):
        if hasattr(self, '_render_timer') and self._render_timer.isActive():
            self._render_timer.stop()

    # ── Mesh Loading ──────────────────────────────────────────────────────────

    def load_mesh(self, model: ModelAsset):
        """Queue a model for GPU upload — actual upload happens in paintGL."""
        self._pending_model  = model
        self._active_lod     = 0   # reset to LOD0 on new model load
        self._cached_material_textures = {}   # clear texture cache for new model
        if hasattr(self, '_cam_logged'):
            del self._cam_logged
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(10, self._trigger_repaint)

    def load_textures(self, material_textures: dict):
        """
        Queue albedo textures for GPU upload on the next paintGL call.
        Always defers to paintGL so OpenGL calls happen on the main thread
        with the GL context guaranteed active.
        """
        if not material_textures:
            return

        # Cache for re-application after LOD switches
        self._cached_material_textures = material_textures
        # Defer actual upload to paintGL (GL context must be on main thread)
        self._pending_textures = material_textures
        self._trigger_repaint()

    def _upload_textures(self, material_textures: dict):
        """Upload RGBA8 pixel data as OpenGL textures and assign to GpuSubMeshes.
        Accepts both old format {mat_idx: (rgba,w,h)} and new format
        {mat_idx: {role: (rgba,w,h,tex_name)}} — uses albedo role for viewport."""
        import ctypes
        gl_tex_map:    dict = {}   # material_index → albedo gl texture id
        gl_nrm_map:    dict = {}   # material_index → normal gl texture id

        for mat_idx, data in material_textures.items():
            # New multi-slot format: {role: (rgba, w, h, tex_name)}
            if isinstance(data, dict):
                # Find the best base_color slot — prefer largest resolution.
                # Multiple base_color slots exist when armorcolor (64×1) and
                # the main diffuse (2048×2048) are both present; pick biggest.
                best_slot = None
                best_pixels = 0
                for role_key, slot_data in data.items():
                    if role_key == 'base_color' or role_key.startswith('base_color_') \
                            or role_key == 'color_id' or role_key.startswith('color_id_'):
                        px = slot_data[1] * slot_data[2]  # w * h
                        if px > best_pixels and slot_data[0]:
                            best_slot = slot_data
                            best_pixels = px
                if best_slot is None:
                    continue
                rgba_bytes, w, h = best_slot[0], best_slot[1], best_slot[2]

                # Also grab normal map slot
                nrm_slot = data.get('normal')
                if nrm_slot is None:
                    for rk, sd in data.items():
                        if rk == 'normal' or rk.startswith('normal_'):
                            nrm_slot = sd
                            break
            else:
                # Legacy format: (rgba, w, h)
                rgba_bytes, w, h = data[0], data[1], data[2]
                nrm_slot = None

            if not rgba_bytes or w == 0 or h == 0:
                continue
            try:
                tex_id = int(glGenTextures(1))
                glBindTexture(GL_TEXTURE_2D, tex_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0,
                             GL_RGBA, GL_UNSIGNED_BYTE,
                             (ctypes.c_uint8 * len(rgba_bytes))(*rgba_bytes))
                glGenerateMipmap(GL_TEXTURE_2D)
                glBindTexture(GL_TEXTURE_2D, 0)
                gl_tex_map[mat_idx] = tex_id
                print(f"[viewport] uploaded texture mat={mat_idx} {w}×{h}")
            except Exception as ex:
                print(f"[viewport] texture upload failed mat={mat_idx}: {ex}")

            # Upload normal map if available
            if nrm_slot and nrm_slot[0]:
                nw, nh = nrm_slot[1], nrm_slot[2]
                try:
                    nrm_id = int(glGenTextures(1))
                    glBindTexture(GL_TEXTURE_2D, nrm_id)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, nw, nh, 0,
                                 GL_RGBA, GL_UNSIGNED_BYTE,
                                 (ctypes.c_uint8 * len(nrm_slot[0]))(*nrm_slot[0]))
                    glGenerateMipmap(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D, 0)
                    gl_nrm_map[mat_idx] = nrm_id
                except Exception as ex:
                    print(f"[viewport] normal upload failed mat={mat_idx}: {ex}")

        # Only update meshes whose material_index appears in the new batch.
        for gm in self._gpu_meshes:
            if gm.material_index in gl_tex_map:
                gm.texture_id = gl_tex_map[gm.material_index]
            if gm.material_index in gl_nrm_map:
                gm.normal_tex_id = gl_nrm_map[gm.material_index]

    def set_lod(self, lod_idx: int):
        """Switch the viewport to show a different LOD level."""
        if self._pending_model is None and not self._gpu_meshes:
            return
        model = getattr(self, '_current_model', None)
        if model is None:
            return
        self._active_lod = lod_idx
        self._pending_model = model   # re-upload with new LOD filter
        # Re-apply cached textures after model re-upload
        if self._cached_material_textures:
            self._pending_textures = self._cached_material_textures
        self._trigger_repaint()

    def _trigger_repaint(self):
        self._redraw()

    def _upload_pending_model(self):
        """Called from paintGL — upload pending model with GL context active."""
        model = self._pending_model
        self._pending_model  = None
        self._current_model  = model   # keep reference for LOD switching

        active_lod = getattr(self, '_active_lod', 0)

        self._free_gpu_meshes()
        all_positions = []
        skipped = 0

        # Fur/composite shell material name patterns
        FUR_KEYWORDS = ('fur', 'compositeshell', 'composite_shell')

        for i, mesh in enumerate(model.meshes):
            # Filter by look 0 + LOD level — avoids bundled props from other looks
            if mesh.look_index != 0 or mesh.lod_level != active_lod:
                continue

            positions, normals, uvs, indices = mesh_to_numpy(model, mesh)
            if positions is None or indices is None or len(positions) == 0:
                skipped += 1
                continue

            gpu = GpuSubMesh()
            gpu.material_index = mesh.material_index

            # Tag fur/shell meshes — they stay in GPU list but can be skipped at draw time
            mat_name = ''
            if model.material_names and mesh.material_index < len(model.material_names):
                mat_name = model.material_names[mesh.material_index].lower()
            gpu.is_fur = any(kw in mat_name for kw in FUR_KEYWORDS)

            try:
                gpu.upload(positions, normals, uvs, indices)
                if gpu.vao == 0:
                    skipped += 1
                    continue
            except Exception as e:
                print(f"[viewport] mesh {i} upload failed: {e}")
                skipped += 1
                continue
            hue = (i * 0.618033) % 1.0
            r, g, b = _hsv_to_rgb(hue, 0.4, 0.85)
            gpu.color = (r, g, b)
            self._gpu_meshes.append(gpu)
            all_positions.append(positions)

        print(f"[viewport] LOD{active_lod}: {len(self._gpu_meshes)} GPU meshes, {skipped} skipped")

        if all_positions:
            pts = np.concatenate(all_positions)
            mn, mx = pts.min(axis=0), pts.max(axis=0)
            self._aabb_min = mn
            self._aabb_max = mx
            self._grid_y   = 0.0  # always at world origin
            self.camera.frame_aabb(mn, mx)

    def frame_model(self):
        """Reset camera to frame the loaded model."""
        if self._gpu_meshes:
            # Re-frame from stored AABB
            if hasattr(self, '_aabb_min') and hasattr(self, '_aabb_max'):
                self.camera.frame_aabb(self._aabb_min, self._aabb_max)
                self._redraw()
        else:
            self.camera.target = np.zeros(3, dtype=np.float32)
            self.camera.dist   = 5.0
            self._redraw()

    def set_view_preset(self, preset: str):
        """Set camera to a named preset view."""
        presets = {
            'main':   ( 45,  25),
            'front':  ( 90,   0),
            'back':   (270,   0),
            'right':  (180,   0),
            'left':   (  0,   0),
            'top':    (  0,  89),
            'bottom': (  0, -89),
        }
        if preset in presets:
            self.camera.yaw, self.camera.pitch = presets[preset]
            self._redraw()

    def clear_mesh(self):
        self.makeCurrent()
        self._free_gpu_meshes()
        self.doneCurrent()
        self._redraw()

    def set_wireframe(self, enabled: bool):
        self._wireframe = enabled
        self._redraw()

    def set_show_fur(self, enabled: bool):
        """Toggle visibility of fur/composite shell meshes."""
        self._show_fur = enabled
        self.update()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    # ── OpenGL Lifecycle ──────────────────────────────────────────────────────

    def initializeGL(self):
        if not _HAS_OPENGL:
            return

        glClearColor(0.102, 0.110, 0.133, 1.0)  # matches BG_BASE #1a1c22
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        vert = compileShader(VERT_SRC, GL_VERTEX_SHADER)
        frag = compileShader(FRAG_SRC, GL_FRAGMENT_SHADER)
        self._shader_prog = compileProgram(vert, frag)

        gv = compileShader(GRID_VERT, GL_VERTEX_SHADER)
        gf = compileShader(GRID_FRAG, GL_FRAGMENT_SHADER)
        self._grid_prog = compileProgram(gv, gf)

        self._build_grid(20, 0.2)

    def resizeGL(self, w: int, h: int):
        glViewport(0, 0, w, h)

    def paintGL(self):
        if not _HAS_OPENGL:
            return

        # Upload any pending model now that GL context is active
        if self._pending_model is not None:
            self._upload_pending_model()

        # Upload any pending textures
        pending_tex = getattr(self, '_pending_textures', None)
        if pending_tex is not None:
            self._pending_textures = None
            self._upload_textures(pending_tex)

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        w, h = self.width(), self.height()
        aspect = w / max(h, 1)
        if getattr(self, '_ortho', False):
            # Orthographic: scale half-height by camera distance.
            # near/far are expressed in view space; we push near well behind
            # the eye (negative) so the infinite grid is never clipped, while
            # keeping far large enough for big scenes.
            half_h = self.camera.dist * 0.5
            extent = max(self.camera.dist * 10.0, 500.0)
            proj   = _ortho(-half_h * aspect, half_h * aspect,
                            -half_h, half_h, -extent, extent)
        else:
            proj   = _perspective(60.0, aspect, 0.01, 1000.0)
        view   = self.camera.view_matrix()
        vp     = proj @ view
        model  = np.eye(4, dtype=np.float32)
        mvp    = proj @ view @ model
        normal_mat = np.linalg.inv(model[:3, :3]).T

        light_dir = np.array([0.6, 1.0, 0.8], np.float32)
        light_dir /= np.linalg.norm(light_dir)
        fill_dir  = np.array([0.0, 0.3, 1.0], np.float32)   # soft front fill
        fill_dir  /= np.linalg.norm(fill_dir)

        # Draw infinite grid using full-screen quad + fragment shader.
        glDisable(GL_DEPTH_TEST)
        glUseProgram(self._grid_prog)
        inv_vp = np.linalg.inv(vp).astype(np.float32)
        _set_uniform_mat4(self._grid_prog, 'uInvVP', inv_vp)
        _set_uniform_bool(self._grid_prog, 'uOrtho', getattr(self, '_ortho', False))
        eye = self.camera.eye_position()
        _set_uniform_3f(self._grid_prog, 'uEye', float(eye[0]), float(eye[1]), float(eye[2]))
        grid_y = getattr(self, '_grid_y', 0.0)
        loc = glGetUniformLocation(self._grid_prog, 'uGridY')
        if loc >= 0: glUniform1f(loc, grid_y)
        if self._grid_vao:
            glBindVertexArray(self._grid_vao)
            glDrawArrays(GL_TRIANGLES, 0, self._grid_count)
            glBindVertexArray(0)
        glEnable(GL_DEPTH_TEST)

        # Draw meshes
        if self._gpu_meshes:
            glUseProgram(self._shader_prog)
            _set_uniform_mat4(self._shader_prog, 'uMVP', mvp)
            _set_uniform_mat4(self._shader_prog, 'uModel', model)
            _set_uniform_mat3(self._shader_prog, 'uNormal', normal_mat)
            _set_uniform_3f(self._shader_prog, 'uLightDir', *light_dir)
            _set_uniform_3f(self._shader_prog, 'uFillDir',  *fill_dir)
            _set_uniform_bool(self._shader_prog, 'uWireframe', self._wireframe)

            # Bind texture samplers
            loc_albedo = glGetUniformLocation(self._shader_prog, 'uAlbedo')
            if loc_albedo >= 0:
                glUniform1i(loc_albedo, 0)
            loc_nrm = glGetUniformLocation(self._shader_prog, 'uNormalMap')
            if loc_nrm >= 0:
                glUniform1i(loc_nrm, 1)

            if self._wireframe:
                glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            else:
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

            for gm in self._gpu_meshes:
                if gm.is_fur and not self._show_fur:
                    continue
                has_tex = gm.texture_id > 0 and not self._wireframe
                has_nrm = gm.normal_tex_id > 0 and not self._wireframe
                _set_uniform_bool(self._shader_prog, 'uHasTexture', has_tex)
                _set_uniform_bool(self._shader_prog, 'uHasNormal', has_nrm)
                _set_uniform_3f(self._shader_prog, 'uBaseColor', *gm.color)
                if has_tex:
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, gm.texture_id)
                if has_nrm:
                    glActiveTexture(GL_TEXTURE1)
                    glBindTexture(GL_TEXTURE_2D, gm.normal_tex_id)
                gm.draw()
                if has_tex:
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, 0)
                if has_nrm:
                    glActiveTexture(GL_TEXTURE1)
                    glBindTexture(GL_TEXTURE_2D, 0)

            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

    # ── Control settings ──────────────────────────────────────────────────────

    def reload_controls(self):
        """Re-read persisted control settings (call after the dialog closes)."""
        self._controls = load_controls()

    # ── Mouse Input  (Blender-style) ──────────────────────────────────────────
    #
    #   LMB drag            → Orbit
    #   MMB drag            → Pan
    #   Shift + MMB drag    → Pan
    #   Ctrl  + MMB drag    → Zoom (vertical drag)
    #   Scroll wheel        → Zoom
    #

    def mousePressEvent(self, e: QMouseEvent):
        self.setFocus()
        self._last_pos = e.pos()
        self._dragging = True

        mods = e.modifiers()
        btn  = e.button()

        LMB = Qt.MouseButton.LeftButton
        MMB = Qt.MouseButton.MiddleButton

        if btn == MMB:
            if mods & Qt.KeyboardModifier.ControlModifier:
                self._mouse_mode = 'zoom_drag'
            else:
                # Shift+MMB and plain MMB both pan
                self._mouse_mode = 'pan'
        elif btn == LMB:
            self._mouse_mode = 'orbit'
        else:
            self._mouse_mode = 'orbit'

        self._start_render_loop()

    def mouseMoveEvent(self, e: QMouseEvent):
        if not self._dragging or self._last_pos is None:
            return

        held = e.buttons()
        LMB  = Qt.MouseButton.LeftButton
        MMB  = Qt.MouseButton.MiddleButton

        if not (held & LMB) and not (held & MMB):
            self._dragging = False
            return

        raw_dx = e.pos().x() - self._last_pos.x()
        raw_dy = e.pos().y() - self._last_pos.y()

        if self._mouse_mode == 'orbit':
            inv_x = self._controls.get("invert_orbit_x", False)
            inv_y = self._controls.get("invert_orbit_y", False)
            dx = -raw_dx if inv_x else raw_dx
            dy = -raw_dy if inv_y else raw_dy
            self.camera.orbit(dx, dy)

        elif self._mouse_mode == 'pan':
            self.camera.pan(raw_dx, raw_dy)

        elif self._mouse_mode == 'zoom_drag':
            # Ctrl+MMB vertical drag: drag down = zoom in (positive delta)
            speed = float(self._controls.get("zoom_speed", 1.0))
            self.camera.zoom(-raw_dy * 0.02 * speed)

        self._last_pos = e.pos()
        try:
            win = self.window()
            if hasattr(win, '_status_lbl'):
                win._status_lbl.setText(
                    f"Camera yaw={self.camera.yaw:.0f}°  pitch={self.camera.pitch:.0f}°"
                )
        except Exception:
            pass

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._dragging = False
        self._last_pos = None
        self._stop_render_loop()
        self.update()

    def wheelEvent(self, e: QWheelEvent):
        raw_delta = e.angleDelta().y() / 120.0   # +1 = scroll up = zoom in
        speed     = float(self._controls.get("zoom_speed", 1.0))
        invert    = self._controls.get("invert_zoom", False)
        signed    = raw_delta if not invert else -raw_delta
        self.camera.zoom(signed * speed)
        self._redraw()

    # ── Keyboard Input (Blender numpad presets) ───────────────────────────────

    def keyPressEvent(self, e):
        key = e.key()
        K   = Qt.Key

        numpad_presets = {
            K.Key_1:        'front',
            K.Key_3:        'right',
            K.Key_7:        'top',
            K.Key_9:        'back',   # Blender: Ctrl+Num1 = back; Num9 is close enough
        }

        if key in numpad_presets:
            self.set_view_preset(numpad_presets[key])
        elif key == K.Key_5:
            self._toggle_ortho()
        elif key == K.Key_F:
            self.frame_model()
        else:
            super().keyPressEvent(e)

    def _toggle_ortho(self):
        """Toggle between perspective and orthographic projection."""
        self._ortho = not getattr(self, '_ortho', False)
        self._redraw()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _free_gpu_meshes(self):
        # Collect unique texture IDs before freeing (multiple meshes may share a texture)
        unique_tex_ids = set()
        for gm in self._gpu_meshes:
            if gm.texture_id:
                unique_tex_ids.add(int(gm.texture_id))
            if gm.normal_tex_id:
                unique_tex_ids.add(int(gm.normal_tex_id))
            gm.texture_id = 0  # prevent gm.free() from deleting it
            gm.normal_tex_id = 0

        for gm in self._gpu_meshes:
            gm.free()
        self._gpu_meshes.clear()

        # Now delete unique textures once each
        if unique_tex_ids:
            try:
                glDeleteTextures(len(unique_tex_ids), list(unique_tex_ids))
            except Exception:
                pass

    def _build_grid(self, half_size: int = 10, spacing: float = 0.1):
        """Build a full-screen quad for the infinite grid fragment shader."""
        verts = np.array([
            -1, -1, 0,   1, -1, 0,   1,  1, 0,
            -1, -1, 0,   1,  1, 0,  -1,  1, 0,
        ], dtype=np.float32)

        self._grid_vao = glGenVertexArrays(1)
        self._grid_vbo = glGenBuffers(1)
        glBindVertexArray(self._grid_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._grid_vbo)
        glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_STATIC_DRAW)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, None)
        glEnableVertexAttribArray(0)
        glBindVertexArray(0)
        self._grid_count = 6


# ── Math helpers ──────────────────────────────────────────────────────────────

def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    return np.array([
        [f/aspect, 0,  0,                        0                       ],
        [0,        f,  0,                        0                       ],
        [0,        0,  (far+near)/(near-far),    (2*far*near)/(near-far) ],
        [0,        0, -1,                        0                       ]
    ], dtype=np.float32)

def _ortho(left: float, right: float, bottom: float, top: float,
           near: float, far: float) -> np.ndarray:
    return np.array([
        [2/(right-left), 0,              0,             -(right+left)/(right-left)],
        [0,              2/(top-bottom), 0,             -(top+bottom)/(top-bottom)],
        [0,              0,             -2/(far-near),  -(far+near)/(far-near)    ],
        [0,              0,              0,              1                        ],
    ], dtype=np.float32)

def _look_at(eye: np.ndarray, center: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = center - eye;  f /= np.linalg.norm(f)
    r = np.cross(f, up); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return np.array([
        [ r[0],  r[1],  r[2], -np.dot(r, eye)],
        [ u[0],  u[1],  u[2], -np.dot(u, eye)],
        [-f[0], -f[1], -f[2],  np.dot(f, eye)],
        [ 0,     0,     0,     1             ]
    ], dtype=np.float32)

def _set_uniform_mat4(prog, name, mat):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        # OpenGL expects column-major; numpy matrices are row-major.
        # GL_TRUE means transpose on upload, so pass as-is with GL_TRUE.
        glUniformMatrix4fv(loc, 1, GL_TRUE, mat.astype(np.float32))

def _set_uniform_mat3(prog, name, mat):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        glUniformMatrix3fv(loc, 1, GL_TRUE, mat.astype(np.float32))

def _set_uniform_3f(prog, name, x, y, z):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        glUniform3f(loc, x, y, z)

def _set_uniform_bool(prog, name, val):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        glUniform1i(loc, int(val))

def _hsv_to_rgb(h, s, v):
    i = int(h * 6)
    f = h * 6 - i
    p = v * (1 - s);  q = v * (1 - f * s);  t = v * (1 - (1 - f) * s)
    i %= 6
    return [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i]
