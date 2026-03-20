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

uniform vec3  uLightDir;
uniform vec3  uBaseColor;
uniform bool  uWireframe;

out vec4 FragColor;

void main() {
    if (uWireframe) {
        FragColor = vec4(0.2, 0.8, 1.0, 1.0);
        return;
    }
    vec3 n    = normalize(vNormal);
    float NdL = max(dot(n, normalize(uLightDir)), 0.0);
    vec3 ambient = uBaseColor * 0.25;
    vec3 diffuse = uBaseColor * NdL;
    vec3 col     = ambient + diffuse;
    // Rim
    col += vec3(0.05, 0.1, 0.2) * pow(1.0 - abs(dot(n, vec3(0,0,1))), 3.0);
    FragColor = vec4(col, 1.0);
}
"""

GRID_VERT = """
#version 330 core
layout(location=0) in vec3 aPos;
uniform mat4 uVP;
out float vFade;
void main() {
    gl_Position = uVP * vec4(aPos, 1.0);
    vFade = 1.0 - clamp(length(aPos.xz) / 50.0, 0.0, 1.0);
}
"""

GRID_FRAG = """
#version 330 core
in float vFade;
out vec4 FragColor;
void main() {
    FragColor = vec4(0.3, 0.35, 0.4, vFade * 0.6);
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
        self.yaw   += dx * 0.5
        self.pitch  = max(-89, min(89, self.pitch - dy * 0.5))

    def pan(self, dx: float, dy: float):
        right = np.array([math.cos(math.radians(self.yaw)), 0,
                          -math.sin(math.radians(self.yaw))], np.float32)
        up    = np.array([0, 1, 0], np.float32)
        speed = self.dist * 0.002
        self.target += (-right * dx + up * dy) * speed

    def zoom(self, delta: float):
        self.dist = max(0.1, self.dist * (1.0 - delta * 0.1))

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
        self._mouse_mode:  str = 'orbit'   # 'orbit' | 'pan'
        self._wireframe:   bool = False
        self.setMinimumSize(400, 300)

    # ── Mesh Loading ──────────────────────────────────────────────────────────

    def load_mesh(self, model: ModelAsset):
        self.makeCurrent()
        self._free_gpu_meshes()

        all_positions = []
        for i, mesh in enumerate(model.meshes):
            positions, normals, uvs, indices = mesh_to_numpy(model, mesh)
            if positions is None or indices is None or len(positions) == 0:
                continue
            gpu = GpuSubMesh()
            gpu.upload(positions, normals, uvs, indices)
            hue = (i * 0.618033) % 1.0
            r, g, b = _hsv_to_rgb(hue, 0.4, 0.85)
            gpu.color = (r, g, b)
            self._gpu_meshes.append(gpu)
            all_positions.append(positions)

        if all_positions:
            pts = np.concatenate(all_positions)
            self.camera.frame_aabb(pts.min(axis=0), pts.max(axis=0))

        self.doneCurrent()
        self.update()

    def clear_mesh(self):
        self.makeCurrent()
        self._free_gpu_meshes()
        self.doneCurrent()
        self.update()

    def set_wireframe(self, enabled: bool):
        self._wireframe = enabled
        self.update()

    # ── OpenGL Lifecycle ──────────────────────────────────────────────────────

    def initializeGL(self):
        if not _HAS_OPENGL:
            return

        glClearColor(0.12, 0.13, 0.16, 1.0)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Mesh shader
        vert = compileShader(VERT_SRC, GL_VERTEX_SHADER)
        frag = compileShader(FRAG_SRC, GL_FRAGMENT_SHADER)
        self._shader_prog = compileProgram(vert, frag)

        # Grid shader
        gv = compileShader(GRID_VERT, GL_VERTEX_SHADER)
        gf = compileShader(GRID_FRAG, GL_FRAGMENT_SHADER)
        self._grid_prog = compileProgram(gv, gf)

        self._build_grid(50, 2.0)

    def resizeGL(self, w: int, h: int):
        glViewport(0, 0, w, h)

    def paintGL(self):
        if not _HAS_OPENGL:
            return

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        w, h = self.width(), self.height()
        aspect = w / max(h, 1)
        proj   = _perspective(60.0, aspect, 0.01, 1000.0)
        view   = self.camera.view_matrix()
        vp     = proj @ view
        model  = np.eye(4, dtype=np.float32)
        mvp    = proj @ view @ model
        normal_mat = np.linalg.inv(model[:3, :3]).T

        light_dir = np.array([0.6, 1.0, 0.8], np.float32)
        light_dir /= np.linalg.norm(light_dir)

        # Draw grid
        glUseProgram(self._grid_prog)
        _set_uniform_mat4(self._grid_prog, 'uVP', vp)
        if self._grid_vao:
            glBindVertexArray(self._grid_vao)
            glDrawArrays(GL_LINES, 0, self._grid_count)
            glBindVertexArray(0)

        # Draw meshes
        if self._gpu_meshes:
            glUseProgram(self._shader_prog)
            _set_uniform_mat4(self._shader_prog, 'uMVP', mvp)
            _set_uniform_mat4(self._shader_prog, 'uModel', model)
            _set_uniform_mat3(self._shader_prog, 'uNormal', normal_mat)
            _set_uniform_3f(self._shader_prog, 'uLightDir', *light_dir)
            _set_uniform_bool(self._shader_prog, 'uWireframe', self._wireframe)

            if self._wireframe:
                glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            else:
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

            for gm in self._gpu_meshes:
                _set_uniform_3f(self._shader_prog, 'uBaseColor', *gm.color)
                gm.draw()

            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)

    # ── Mouse Input ───────────────────────────────────────────────────────────

    def mousePressEvent(self, e: QMouseEvent):
        self._last_pos = e.pos()
        if e.buttons() & Qt.MouseButton.MiddleButton:
            self._mouse_mode = 'pan'
        elif e.buttons() & Qt.MouseButton.RightButton:
            self._mouse_mode = 'orbit'
        else:
            self._mouse_mode = 'orbit'

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._last_pos is None:
            return
        dx = e.pos().x() - self._last_pos.x()
        dy = e.pos().y() - self._last_pos.y()
        if self._mouse_mode == 'orbit':
            self.camera.orbit(dx, dy)
        else:
            self.camera.pan(dx, dy)
        self._last_pos = e.pos()
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._last_pos = None

    def wheelEvent(self, e: QWheelEvent):
        delta = e.angleDelta().y() / 120.0
        self.camera.zoom(-delta)
        self.update()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _free_gpu_meshes(self):
        for gm in self._gpu_meshes:
            gm.free()
        self._gpu_meshes.clear()

    def _build_grid(self, half_size: int, spacing: float):
        lines = []
        for i in range(-half_size, half_size + 1):
            x = i * spacing
            lines += [x, 0, -half_size * spacing,  x, 0, half_size * spacing]
            lines += [-half_size * spacing, 0, x,   half_size * spacing, 0, x]
        verts = np.array(lines, dtype=np.float32)

        self._grid_vao = glGenVertexArrays(1)
        self._grid_vbo = glGenBuffers(1)
        glBindVertexArray(self._grid_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self._grid_vbo)
        glBufferData(GL_ARRAY_BUFFER, verts.nbytes, verts, GL_STATIC_DRAW)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 12, None)
        glEnableVertexAttribArray(0)
        glBindVertexArray(0)
        self._grid_count = len(verts) // 3


# ── Math helpers ──────────────────────────────────────────────────────────────

def _perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = -1.0
    m[3, 2] = (2 * far * near) / (near - far)
    return m

def _look_at(eye: np.ndarray, center: np.ndarray, up: np.ndarray) -> np.ndarray:
    f = center - eye;  f /= np.linalg.norm(f)
    s = np.cross(f, up); s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4, dtype=np.float32)
    m[0, :3] = s;  m[1, :3] = u;  m[2, :3] = -f
    m[3, 0] = -np.dot(s, eye)
    m[3, 1] = -np.dot(u, eye)
    m[3, 2] =  np.dot(f, eye)
    return m.T

def _set_uniform_mat4(prog, name, mat):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        glUniformMatrix4fv(loc, 1, GL_FALSE, mat.flatten(order='F'))

def _set_uniform_mat3(prog, name, mat):
    loc = glGetUniformLocation(prog, name)
    if loc >= 0:
        glUniformMatrix3fv(loc, 1, GL_FALSE, mat.flatten(order='F'))

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
