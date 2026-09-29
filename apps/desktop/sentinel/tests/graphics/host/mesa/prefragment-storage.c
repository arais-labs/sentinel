/* Hardware acceptance fixture, not full conformance. No capability overrides.
 * Build like mip-views.c (epoxy EGL/GL); optionally select vs/tcs/tes/gs/pure-gs.
 * `gs N` selects one independently reproducible case, N in [0,23]:
 * N = draw_kind*8 + rasterizer_discard*4 + transform_feedback*2 + zero_emission.
 * Exit 77 means required genuine per-stage limits are absent, NOT a pass. */
#include <epoxy/egl.h>
#include <epoxy/gl.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIDTH 64
#define HEIGHT 32
#define WORDS 64
#define GUARD UINT32_C(0xdeadbeef)
#define REQUIRE(test) do { if (!(test)) { \
   fprintf(stderr, "FAIL %s:%d: %s (GL=%#x)\n", __func__, __LINE__, #test, glGetError()); \
   return 0; \
} } while (0)
#define CLEAN() REQUIRE(glGetError() == GL_NO_ERROR)

static const char *version = "#version 430 core\n";
static const char *storage =
   "layout(std430,binding=0) buffer Storage { uint words[]; };\n"
   "layout(r32ui,binding=0) uniform uimage2D image;\n"
   "void store_one(uint id) {\n"
   " uint old=atomicAdd(words[0],1u);\n"
   " atomicMax(words[1],old); atomicOr(words[2],1u<<(old%32u));\n"
   " words[8u+id]=0x12340000u+id;\n"
   " uint image_old=imageAtomicAdd(image,ivec2(0,0),1u);\n"
   " imageAtomicMax(image,ivec2(1,0),image_old);\n"
   " imageStore(image,ivec2(8u+id,0),uvec4(0x43210000u+id));\n"
   "}\n";

static GLuint shader(GLenum stage, const char *declarations, const char *body)
{
   GLuint object = glCreateShader(stage);
   const char *parts[] = {version, declarations, body};
   glShaderSource(object, 3, parts, NULL);
   glCompileShader(object);
   GLint ok;
   glGetShaderiv(object, GL_COMPILE_STATUS, &ok);
   if (!ok) {
      char log[8192];
      glGetShaderInfoLog(object, sizeof(log), NULL, log);
      fprintf(stderr, "Shader stage %#x failed: %s\n", stage, log);
      glDeleteShader(object);
      return 0;
   }
   return object;
}

static int attach(GLuint program, GLenum stage, const char *declarations, const char *body)
{
   GLuint object = shader(stage, declarations, body);
   REQUIRE(object);
   glAttachShader(program, object);
   glDeleteShader(object);
   return 1;
}

static int link_program(GLuint program, int capture)
{
   if (capture) {
      const char *varyings[] = {"record", "gl_Position"};
      glTransformFeedbackVaryings(program, 2, varyings, GL_INTERLEAVED_ATTRIBS);
   }
   glLinkProgram(program);
   GLint ok;
   glGetProgramiv(program, GL_LINK_STATUS, &ok);
   if (!ok) {
      char log[8192];
      glGetProgramInfoLog(program, sizeof(log), NULL, log);
      fprintf(stderr, "Program link failed: %s\n", log);
   }
   REQUIRE(ok);
   glUseProgram(program);
   return 1;
}

struct backing { GLuint ssbo, image; };

static void backing_create(struct backing *backing)
{
   uint32_t initial[WORDS];
   for (unsigned i = 0; i < WORDS; i++) initial[i] = GUARD;
   initial[0] = initial[1] = initial[2] = 0;
   glGenBuffers(1, &backing->ssbo);
   glBindBuffer(GL_SHADER_STORAGE_BUFFER, backing->ssbo);
   glBufferData(GL_SHADER_STORAGE_BUFFER, sizeof(initial), initial, GL_DYNAMIC_COPY);
   glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, backing->ssbo);
   glGenTextures(1, &backing->image);
   glBindTexture(GL_TEXTURE_2D, backing->image);
   glTexStorage2D(GL_TEXTURE_2D, 1, GL_R32UI, WORDS, 1);
   glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, WORDS, 1, GL_RED_INTEGER, GL_UNSIGNED_INT, initial);
   glBindImageTexture(0, backing->image, 0, GL_FALSE, 0, GL_READ_WRITE, GL_R32UI);
}

static int complete(void)
{
   glMemoryBarrier(GL_BUFFER_UPDATE_BARRIER_BIT | GL_TEXTURE_UPDATE_BARRIER_BIT |
                   GL_TRANSFORM_FEEDBACK_BARRIER_BIT | GL_FRAMEBUFFER_BARRIER_BIT);
   GLsync fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
   REQUIRE(fence);
   GLenum result = glClientWaitSync(fence, GL_SYNC_FLUSH_COMMANDS_BIT, 5000000000ull);
   glDeleteSync(fence);
   REQUIRE(result == GL_ALREADY_SIGNALED || result == GL_CONDITION_SATISFIED);
   CLEAN();
   return 1;
}

static void backing_read(const struct backing *backing, uint32_t words[WORDS], uint32_t image[WORDS])
{
   glBindBuffer(GL_SHADER_STORAGE_BUFFER, backing->ssbo);
   glGetBufferSubData(GL_SHADER_STORAGE_BUFFER, 0, WORDS * sizeof(uint32_t), words);
   glBindTexture(GL_TEXTURE_2D, backing->image);
   glGetTexImage(GL_TEXTURE_2D, 0, GL_RED_INTEGER, GL_UNSIGNED_INT, image);
}

static void backing_delete(const struct backing *backing)
{
   glDeleteBuffers(1, &backing->ssbo);
   glDeleteTextures(1, &backing->image);
}

static int check_stores(const char *name, const struct backing *backing, unsigned ids)
{
   uint32_t words[WORDS], image[WORDS];
   backing_read(backing, words, image);
   CLEAN();
   /* Vertex/TES caching may change execution counts. Require real stores and
    * an internally exact relationship to returned atomic values, not a guessed
    * number of shader invocations. */
   REQUIRE(words[0] >= ids);
   REQUIRE(words[1] == words[0] - 1);
   REQUIRE(words[2] == (words[0] >= 32 ? UINT32_MAX : (1u << words[0]) - 1));
   REQUIRE(image[0] == words[0] && image[1] == image[0] - 1);
   for (unsigned i = 0; i < ids; i++) {
      REQUIRE(words[8 + i] == 0x12340000u + i);
      REQUIRE(image[8 + i] == 0x43210000u + i);
   }
   for (unsigned i = 3; i < WORDS; i++) {
      if (i >= 8 && i < 8 + ids) continue;
      REQUIRE(words[i] == GUARD && image[i] == GUARD);
   }
   REQUIRE(image[2] == 0);
   printf("PASS %s SSBO/image stores, atomic-return relationships, guards; invocations=%u\n", name, words[0]);
   return 1;
}

static int simple_stage(const char *name)
{
   int vertex = !strcmp(name, "vs"), control = !strcmp(name, "tcs");
   GLuint program = glCreateProgram();
   const char *plain_vs = "void main(){gl_Position=vec4(0,0,0,1);}";
   const char *store_vs = "void main(){store_one(uint(gl_VertexID));gl_Position=vec4(0,0,0,1);}";
   REQUIRE(attach(program, GL_VERTEX_SHADER, vertex ? storage : "", vertex ? store_vs : plain_vs));
   if (!vertex) {
      const char *plain_tcs =
         "layout(vertices=3) out;void main(){gl_out[gl_InvocationID].gl_Position=gl_in[gl_InvocationID].gl_Position;"
         "if(gl_InvocationID==0){gl_TessLevelOuter[0]=1;gl_TessLevelOuter[1]=1;gl_TessLevelOuter[2]=1;gl_TessLevelInner[0]=1;}}";
      const char *store_tcs =
         "layout(vertices=3) out;void main(){store_one(uint(gl_InvocationID));"
         "gl_out[gl_InvocationID].gl_Position=gl_in[gl_InvocationID].gl_Position;"
         "if(gl_InvocationID==0){gl_TessLevelOuter[0]=1;gl_TessLevelOuter[1]=1;gl_TessLevelOuter[2]=1;gl_TessLevelInner[0]=1;}}";
      const char *plain_tes = "layout(triangles,equal_spacing,ccw) in;void main(){gl_Position=vec4(gl_TessCoord.xy,0,1);}";
      const char *store_tes =
         "layout(triangles,equal_spacing,ccw) in;void main(){"
         "uint id=gl_TessCoord.x>0.5?0u:(gl_TessCoord.y>0.5?1u:2u);store_one(id);"
         "gl_Position=vec4(gl_TessCoord.xy,0,1);}";
      REQUIRE(attach(program, GL_TESS_CONTROL_SHADER, control ? storage : "", control ? store_tcs : plain_tcs));
      REQUIRE(attach(program, GL_TESS_EVALUATION_SHADER, control ? "" : storage, control ? plain_tes : store_tes));
   }
   REQUIRE(attach(program, GL_FRAGMENT_SHADER, "", "out vec4 color;void main(){color=vec4(1);}"));
   REQUIRE(link_program(program, 0));
   struct backing backing;
   backing_create(&backing);
   glEnable(GL_RASTERIZER_DISCARD);
   if (!vertex) glPatchParameteri(GL_PATCH_VERTICES, 3);
   glDrawArrays(vertex ? GL_POINTS : GL_PATCHES, 0, 3);
   REQUIRE(complete());
   REQUIRE(check_stores(name, &backing, 3));
   glDisable(GL_RASTERIZER_DISCARD);
   backing_delete(&backing);
   glDeleteProgram(program);
   return 1;
}

enum draw_kind { DIRECT, INDEXED, INDIRECT };

static int geometry_case(enum draw_kind kind, int discard, int capture, int empty)
{
   printf("CASE GS draw=%d discard=%d xfb=%d empty=%d\n", kind, discard, capture, empty);
   GLuint program = glCreateProgram();
   REQUIRE(attach(program, GL_VERTEX_SHADER, "", "void main(){gl_Position=vec4(0,0,0,1);}"));
   char body[2048];
   snprintf(body, sizeof(body),
      "layout(points,invocations=2) in;layout(points,max_vertices=3) out;flat out uint record;"
      "void main(){uint id=uint(gl_PrimitiveIDIn)*2u+uint(gl_InvocationID);"
      "uint old=atomicAdd(words[0],1u);uint io=imageAtomicAdd(image,ivec2(0,0),1u);"
      "words[8u+id]=old;imageStore(image,ivec2(8u+id,0),uvec4(io));"
      "for(uint e=0u;e<%s;e++){record=old*16u+e;"
      "gl_Position=vec4((float(old*4u)+2.5)/64.0*2.0-1.0,(float(e*4u)+2.5)/32.0*2.0-1.0,0,1);"
      "gl_PointSize=1;EmitVertex();EndPrimitive();}}", empty ? "0u" : "(old%3u+1u)");
   REQUIRE(attach(program, GL_GEOMETRY_SHADER, storage, body));
   REQUIRE(attach(program, GL_FRAGMENT_SHADER, "", "flat in uint record;out vec4 color;void main(){color=vec4(float(record+1u)/255.0,0,0,1);}"));
   REQUIRE(link_program(program, capture));
   struct backing backing;
   backing_create(&backing);
   GLuint extra[3], query;
   glGenBuffers(3, extra);
   const uint32_t indices[] = {2, 0, 1};
   glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, extra[0]);
   glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(indices), indices, GL_STATIC_DRAW);
   const uint32_t indirect[] = {3, 1, 0, 0};
   glBindBuffer(GL_DRAW_INDIRECT_BUFFER, extra[1]);
   glBufferData(GL_DRAW_INDIRECT_BUFFER, sizeof(indirect), indirect, GL_STATIC_DRAW);
   uint32_t feedback[5 * 24];
   for (unsigned i = 0; i < sizeof(feedback) / sizeof(feedback[0]); i++) feedback[i] = GUARD;
   glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER, extra[2]);
   glBufferData(GL_TRANSFORM_FEEDBACK_BUFFER, sizeof(feedback), feedback, GL_DYNAMIC_COPY);
   glBindBufferBase(GL_TRANSFORM_FEEDBACK_BUFFER, 0, extra[2]);
   glGenQueries(1, &query);
   glViewport(0, 0, WIDTH, HEIGHT);
   glDisable(GL_RASTERIZER_DISCARD);
   glClearColor(0, 0, 0, 0);
   glClear(GL_COLOR_BUFFER_BIT);
   if (discard) glEnable(GL_RASTERIZER_DISCARD);
   if (capture) {
      glBeginQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN, query);
      glBeginTransformFeedback(GL_POINTS);
   }
   switch (kind) {
   case DIRECT: glDrawArrays(GL_POINTS, 0, 3); break;
   case INDEXED: glDrawElements(GL_POINTS, 3, GL_UNSIGNED_INT, NULL); break;
   case INDIRECT: glDrawArraysIndirect(GL_POINTS, NULL); break;
   }
   if (capture) { glEndTransformFeedback(); glEndQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN); }
   REQUIRE(complete());
   glDisable(GL_RASTERIZER_DISCARD);
   uint32_t words[WORDS], image[WORDS];
   backing_read(&backing, words, image);
   if (words[0] != 6 || image[0] != 6)
      fprintf(stderr, "GS counts: SSBO=%u image=%u, expected exactly 6 each\n", words[0], image[0]);
   REQUIRE(words[0] == 6 && image[0] == 6);
   unsigned seen = 0, image_seen = 0;
   for (unsigned i = 8; i < 14; i++) {
      REQUIRE(words[i] < 6 && image[i] < 6);
      seen |= 1u << words[i]; image_seen |= 1u << image[i];
   }
   REQUIRE(seen == 63 && image_seen == 63);
   for (unsigned i = 1; i < WORDS; i++) {
      if (i >= 8 && i < 14) continue;
      REQUIRE(words[i] == (i <= 2 ? 0u : GUARD));
      REQUIRE(image[i] == (i <= 2 ? 0u : GUARD));
   }
   const unsigned emitted = empty ? 0 : 12;
   if (capture) {
      GLuint primitives;
      glGetQueryObjectuiv(query, GL_QUERY_RESULT, &primitives);
      REQUIRE(primitives == emitted);
      glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER, extra[2]);
      glGetBufferSubData(GL_TRANSFORM_FEEDBACK_BUFFER, 0, sizeof(feedback), feedback);
      uint32_t records[6] = {0};
      for (unsigned i = 0; i < emitted; i++) {
         unsigned record = feedback[i * 5], old = record / 16, e = record % 16;
         REQUIRE(old < 6 && e < old % 3 + 1);
         REQUIRE(!(records[old] & (1u << e)));
         records[old] |= 1u << e;
         float position[4];
         memcpy(position, feedback + i * 5 + 1, sizeof(position));
         REQUIRE(fabsf(position[0] - ((old * 4 + 2.5f) / WIDTH * 2 - 1)) < 0.00001f);
         REQUIRE(fabsf(position[1] - ((e * 4 + 2.5f) / HEIGHT * 2 - 1)) < 0.00001f);
         REQUIRE(position[2] == 0 && position[3] == 1);
      }
      for (unsigned old = 0; old < 6; old++) REQUIRE(records[old] == (empty ? 0u : (1u << (old % 3 + 1)) - 1));
      for (unsigned i = emitted * 5; i < sizeof(feedback) / sizeof(feedback[0]); i++) REQUIRE(feedback[i] == GUARD);
   }
   unsigned char pixels[WIDTH * HEIGHT * 4];
   glReadPixels(0, 0, WIDTH, HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
   for (unsigned y = 0; y < HEIGHT; y++) for (unsigned x = 0; x < WIDTH; x++) {
      unsigned old = x / 4, e = y / 4;
      int drawn = !discard && !empty && x % 4 == 2 && y % 4 == 2 && old < 6 && e < old % 3 + 1;
      unsigned char *pixel = pixels + (y * WIDTH + x) * 4;
      if (abs((int)pixel[0] - (drawn ? (int)(old * 16 + e + 1) : 0)) > 1 ||
          pixel[1] || pixel[2] || pixel[3] != (drawn ? 255 : 0))
         fprintf(stderr, "GS pixel (%u,%u): RGBA=%u,%u,%u,%u expected=%u,0,0,%u\n",
                 x, y, pixel[0], pixel[1], pixel[2], pixel[3], drawn ? old * 16 + e + 1 : 0, drawn ? 255 : 0);
      REQUIRE(abs((int)pixel[0] - (drawn ? (int)(old * 16 + e + 1) : 0)) <= 1);
      REQUIRE(pixel[1] == 0 && pixel[2] == 0 && pixel[3] == (drawn ? 255 : 0));
   }
   CLEAN();
   glDeleteQueries(1, &query);
   glDeleteBuffers(3, extra);
   backing_delete(&backing);
   glDeleteProgram(program);
   puts("PASS GS exact side-effect counts, atomic-return output, XFB/raster and guards");
   return 1;
}

static int limits(const char *name, GLenum ssbo, GLenum image)
{
   GLint buffers = 0, images = 0;
   glGetIntegerv(ssbo, &buffers); glGetIntegerv(image, &images);
   printf("LIMIT %s ssbo=%d images=%d\n", name, buffers, images);
   return buffers > 0 && images > 0;
}

/* Exercise the non-materialized (side-effect-free) GS path independently of
 * the atomic matrix. Capturing gl_Position must not widen the scalar raster
 * interface; component 2 must retain its leading holes when linking to FS. */
static int pure_geometry_case(unsigned component, int discard)
{
   printf("CASE pure GS scalar component=%u xfb=1 discard=%d\n", component, discard);
   GLuint program = glCreateProgram();
   REQUIRE(attach(program, GL_VERTEX_SHADER, "", "void main(){gl_Position=vec4(0,0,0,1);}"));
   char gs[1024], fs[512];
   snprintf(gs, sizeof(gs),
      "layout(points) in;layout(points,max_vertices=2) out;"
      "layout(location=0,component=%u) flat out uint record;"
      "void main(){for(uint i=0u;i<2u;i++){record=37u+i*83u;"
      "gl_Position=vec4((16.5+float(i)*32.0)/64.0*2.0-1.0,16.5/32.0*2.0-1.0,0,1);"
      "gl_PointSize=1;EmitVertex();EndPrimitive();}}", component);
   snprintf(fs, sizeof(fs),
      "layout(location=0,component=%u) flat in uint record;"
      "out vec4 color;void main(){color=vec4(float(record)/255.0,0,0,1);}", component);
   const char *extension = "#extension GL_ARB_enhanced_layouts : require\n";
   REQUIRE(attach(program, GL_GEOMETRY_SHADER, extension, gs));
   REQUIRE(attach(program, GL_FRAGMENT_SHADER, extension, fs));
   REQUIRE(link_program(program, 1));
   GLuint buffer, query;
   uint32_t feedback[16];
   for (unsigned i = 0; i < 16; i++) feedback[i] = GUARD;
   glGenBuffers(1, &buffer);
   glBindBuffer(GL_TRANSFORM_FEEDBACK_BUFFER, buffer);
   glBufferData(GL_TRANSFORM_FEEDBACK_BUFFER, sizeof(feedback), feedback, GL_DYNAMIC_COPY);
   glBindBufferBase(GL_TRANSFORM_FEEDBACK_BUFFER, 0, buffer);
   glGenQueries(1, &query);
   glViewport(0, 0, WIDTH, HEIGHT);
   glDisable(GL_RASTERIZER_DISCARD);
   glClearColor(0, 0, 0, 0);
   glClear(GL_COLOR_BUFFER_BIT);
   if (discard) glEnable(GL_RASTERIZER_DISCARD);
   glBeginQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN, query);
   glBeginTransformFeedback(GL_POINTS);
   glDrawArrays(GL_POINTS, 0, 1);
   glEndTransformFeedback();
   glEndQuery(GL_TRANSFORM_FEEDBACK_PRIMITIVES_WRITTEN);
   REQUIRE(complete());
   glDisable(GL_RASTERIZER_DISCARD);
   GLuint primitives;
   glGetQueryObjectuiv(query, GL_QUERY_RESULT, &primitives);
   REQUIRE(primitives == 2);
   glGetBufferSubData(GL_TRANSFORM_FEEDBACK_BUFFER, 0, sizeof(feedback), feedback);
   for (unsigned i = 0; i < 2; i++) {
      REQUIRE(feedback[i * 5] == 37 + i * 83);
      float position[4];
      memcpy(position, feedback + i * 5 + 1, sizeof(position));
      REQUIRE(fabsf(position[0] - ((16.5f + i * 32) / WIDTH * 2 - 1)) < 0.00001f);
      REQUIRE(fabsf(position[1] - (16.5f / HEIGHT * 2 - 1)) < 0.00001f);
      REQUIRE(position[2] == 0 && position[3] == 1);
   }
   for (unsigned i = 10; i < 16; i++) REQUIRE(feedback[i] == GUARD);
   unsigned char pixels[WIDTH * HEIGHT * 4];
   glReadPixels(0, 0, WIDTH, HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
   for (unsigned y = 0; y < HEIGHT; y++) for (unsigned x = 0; x < WIDTH; x++) {
      int drawn = !discard && y == 16 && (x == 16 || x == 48);
      unsigned red = drawn ? (x == 16 ? 37 : 120) : 0;
      unsigned char *pixel = pixels + (y * WIDTH + x) * 4;
      REQUIRE(abs((int)pixel[0] - (int)red) <= 1);
      REQUIRE(pixel[1] == 0 && pixel[2] == 0 && pixel[3] == (drawn ? 255 : 0));
   }
   CLEAN();
   glDeleteQueries(1, &query);
   glDeleteBuffers(1, &buffer);
   glDeleteProgram(program);
   puts("PASS pure GS scalar/gl_Position XFB, component interface, raster and guards");
   return 1;
}

int main(int argc, char **argv)
{
   setvbuf(stdout, NULL, _IONBF, 0);
   int selected_case = -1;
   if (argc > 3 || (argc >= 2 && strcmp(argv[1], "vs") && strcmp(argv[1], "tcs") && strcmp(argv[1], "tes") && strcmp(argv[1], "gs") && strcmp(argv[1], "pure-gs"))) return 2;
   if (argc == 3) {
      char *end;
      long value = strtol(argv[2], &end, 10);
      if (strcmp(argv[1], "gs") || !*argv[2] || *end || value < 0 || value >= 24) return 2;
      selected_case = (int)value;
   }
   if (getenv("MESA_GL_VERSION_OVERRIDE") || getenv("MESA_GLSL_VERSION_OVERRIDE") || getenv("MESA_EXTENSION_OVERRIDE") || getenv("MESA_KK_EXPERIMENTAL")) return 2;
   EGLDisplay display = eglGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, NULL, NULL);
   if (!eglInitialize(display, NULL, NULL) || !eglBindAPI(EGL_OPENGL_API)) return 2;
   const EGLint config_attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
      EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
   EGLConfig config; EGLint count;
   if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || !count) return 2;
   const EGLint context_attributes[] = {EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 3,
      EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL_NONE};
   const EGLint surface_attributes[] = {EGL_WIDTH, WIDTH, EGL_HEIGHT, HEIGHT, EGL_NONE};
   EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
   EGLSurface surface = eglCreatePbufferSurface(display, config, surface_attributes);
   if (context == EGL_NO_CONTEXT || surface == EGL_NO_SURFACE || !eglMakeCurrent(display, surface, surface, context)) return 2;
   const char *renderer = (const char *)glGetString(GL_RENDERER);
   printf("GL_VERSION=%s\nGL_RENDERER=%s\n", glGetString(GL_VERSION), renderer);
   if (!renderer || strstr(renderer, "llvmpipe") || strstr(renderer, "softpipe") || strstr(renderer, "SwiftShader")) return 2;
   GLuint vao; glGenVertexArrays(1, &vao); glBindVertexArray(vao);
   const char *names[] = {"vs", "tcs", "tes", "gs"};
   const GLenum ssbo[] = {GL_MAX_VERTEX_SHADER_STORAGE_BLOCKS, GL_MAX_TESS_CONTROL_SHADER_STORAGE_BLOCKS,
      GL_MAX_TESS_EVALUATION_SHADER_STORAGE_BLOCKS, GL_MAX_GEOMETRY_SHADER_STORAGE_BLOCKS};
   const GLenum images[] = {GL_MAX_VERTEX_IMAGE_UNIFORMS, GL_MAX_TESS_CONTROL_IMAGE_UNIFORMS,
      GL_MAX_TESS_EVALUATION_IMAGE_UNIFORMS, GL_MAX_GEOMETRY_IMAGE_UNIFORMS};
   int result = 0;
   for (unsigned stage = 0; stage < 4 && !result; stage++) {
      if (argc >= 2 && strcmp(argv[1], names[stage])) continue;
      if (!limits(names[stage], ssbo[stage], images[stage])) { puts("UNSUPPORTED: genuine stage limits absent; no pass claimed"); result = 77; break; }
      if (stage < 3) { if (!simple_stage(names[stage])) result = 1; }
      else for (unsigned draw = 0; draw < 3 && !result; draw++)
         for (unsigned discard = 0; discard < 2 && !result; discard++)
            for (unsigned capture = 0; capture < 2 && !result; capture++)
               for (unsigned empty = 0; empty < 2 && !result; empty++) {
                  if (selected_case >= 0 && (unsigned)selected_case != draw * 8 + discard * 4 + capture * 2 + empty) continue;
                  if (!geometry_case(draw, discard, capture, empty)) result = 1;
               }
   }
   if (!result && (argc == 1 || !strcmp(argv[1], "pure-gs"))) {
      for (unsigned component = 0; component <= 2 && !result; component += 2)
         for (unsigned discard = 0; discard < 2 && !result; discard++)
            if (!pure_geometry_case(component, discard)) result = 1;
   }
   glDeleteVertexArrays(1, &vao);
   eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
   eglDestroySurface(display, surface); eglDestroyContext(display, context); eglTerminate(display);
   if (!result) puts("PASS bounded prefragment storage/atomic gate (not full conformance)");
   return result;
}
