/* ANGLE exposes Metal multisample textures as an ES 3.0 extension. */
#include <epoxy/egl.h>
#include <epoxy/gl.h>

static inline void sentinel_tex_storage_multisample(GLenum target, GLsizei samples,
    GLenum format, GLsizei width, GLsizei height, GLboolean fixed)
{
    if (!epoxy_is_desktop_gl() && epoxy_gl_version() < 31 &&
        epoxy_has_gl_extension("GL_ANGLE_texture_multisample")) {
        PFNGLTEXSTORAGE2DMULTISAMPLEPROC storage =
            (PFNGLTEXSTORAGE2DMULTISAMPLEPROC)eglGetProcAddress("glTexStorage2DMultisampleANGLE");
        storage(target, samples, format, width, height, fixed);
    } else {
        glTexStorage2DMultisample(target, samples, format, width, height, fixed);
    }
}
