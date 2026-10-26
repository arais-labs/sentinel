// d3d11_probe: the e2e harness's Windows GPU checks (DESIGN.md §25), run in the guest from the share
// (windows\e2e-d3d11.exe), in the signed-in user's session. Direct3D 11 on the first adapter: prints its
// name and PCI ids, renders a triangle offscreen (shaders compiled at run time with Windows'
// d3dcompiler_47), reads it back and checks the pixels. With --vulkan, Vulkan instead: the loader
// (vulkan-1.dll, loaded at run time) lists the physical devices; Venus must be one. With --d3d-vulkan,
// both in this one process, Direct3D first (Neptune's and Venus's transports side by side, DESIGN.md
// §16). Exit code 0 when it passes.
#define COBJMACROS
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef HRESULT(WINAPI *Compile)(LPCVOID, SIZE_T, LPCSTR, const void *, void *, LPCSTR, LPCSTR, UINT, UINT, ID3DBlob **,
                                 ID3DBlob **);

static const char *source =
    "float4 vs(uint id : SV_VertexID) : SV_Position {"
    "  float2 p[3] = { float2(0, 0.5), float2(0.5, -0.5), float2(-0.5, -0.5) };"
    "  return float4(p[id], 0, 1); }"
    "float4 ps() : SV_Target { return float4(1, 0.5, 0, 1); }";

#define CHECK(x)                                                    \
  do {                                                              \
    HRESULT hr_ = (x);                                              \
    if (FAILED(hr_)) {                                              \
      printf("FAIL %s = 0x%lx\n", #x, (unsigned long)hr_);          \
      return 1;                                                     \
    }                                                               \
  } while (0)

// The few Vulkan 1.0 definitions the device listing needs (no Vulkan headers in the toolchain).
typedef struct {
  int sType;
  const void *pNext;
  const char *pApplicationName;
  uint32_t applicationVersion;
  const char *pEngineName;
  uint32_t engineVersion;
  uint32_t apiVersion;
} VkApplicationInfo;
typedef struct {
  int sType;
  const void *pNext;
  uint32_t flags;
  const VkApplicationInfo *pApplicationInfo;
  uint32_t enabledLayerCount;
  const char *const *ppEnabledLayerNames;
  uint32_t enabledExtensionCount;
  const char *const *ppEnabledExtensionNames;
} VkInstanceCreateInfo;
typedef struct {  // VkPhysicalDeviceProperties up to deviceName; the rest is room
  uint32_t apiVersion, driverVersion, vendorID, deviceID;
  int deviceType;
  char deviceName[256];
  unsigned char rest[1024];
} VkPhysicalDeviceProperties;
typedef void *VkInstance;
typedef void *VkPhysicalDevice;
typedef void (*VkVoidFunction)(void);
typedef VkVoidFunction(WINAPI *GetInstanceProcAddr)(VkInstance, const char *);
typedef int(WINAPI *CreateInstance)(const VkInstanceCreateInfo *, const void *, VkInstance *);
typedef int(WINAPI *EnumeratePhysicalDevices)(VkInstance, uint32_t *, VkPhysicalDevice *);
typedef void(WINAPI *GetPhysicalDeviceProperties)(VkPhysicalDevice, VkPhysicalDeviceProperties *);

static const char *volatile stage = "vulkan-1.dll";

static DWORD WINAPI vulkan(void *unused) {
  (void)unused;
  HMODULE loader = LoadLibraryA("vulkan-1.dll");
  GetInstanceProcAddr gipa = loader ? (GetInstanceProcAddr)(void *)GetProcAddress(loader, "vkGetInstanceProcAddr") : NULL;
  if (!gipa) {
    printf("FAIL vulkan: no vulkan-1.dll\n");
    return 1;
  }
  const VkApplicationInfo app = {0 /* APPLICATION_INFO */, NULL, "e2e", 1, NULL, 0, 1u << 22 /* 1.0 */};
  const VkInstanceCreateInfo info = {1 /* INSTANCE_CREATE_INFO */, NULL, 0, &app, 0, NULL, 0, NULL};
  VkInstance instance;
  stage = "vkCreateInstance";
  int r = ((CreateInstance)(void *)gipa(NULL, "vkCreateInstance"))(&info, NULL, &instance);
  if (r) {
    printf("FAIL vulkan: vkCreateInstance = %d\n", r);
    return 1;
  }
  EnumeratePhysicalDevices enumerate = (EnumeratePhysicalDevices)(void *)gipa(instance, "vkEnumeratePhysicalDevices");
  GetPhysicalDeviceProperties properties =
      (GetPhysicalDeviceProperties)(void *)gipa(instance, "vkGetPhysicalDeviceProperties");
  VkPhysicalDevice devices[8];
  uint32_t count = 8;
  stage = "vkEnumeratePhysicalDevices";
  r = enumerate(instance, &count, devices);
  if (r < 0 || !count) {
    printf("FAIL vulkan: vkEnumeratePhysicalDevices = %d, %u devices\n", r, count);
    return 1;
  }
  int venus = 0;
  for (uint32_t i = 0; i < count; i++) {
    VkPhysicalDeviceProperties p;
    stage = "vkGetPhysicalDeviceProperties";
    properties(devices[i], &p);
    printf("vulkan device %s, api %u.%u.%u\n", p.deviceName, p.apiVersion >> 22, (p.apiVersion >> 12) & 0x3ff,
           p.apiVersion & 0xfff);
    venus |= strstr(p.deviceName, "Venus") != NULL;
  }
  printf("%s\n", venus ? "PASS vulkan Venus" : "FAIL vulkan: no Venus device");
  return venus ? 0 : 1;
}

// A Vulkan driver that never answers fails the check instead of hanging it.
static int vulkan_timed(void) {
  HANDLE thread = CreateThread(NULL, 0, vulkan, NULL, 0, NULL);
  DWORD vk = 1;
  if (WaitForSingleObject(thread, 30000) != WAIT_OBJECT_0) {
    printf("FAIL vulkan: %s did not return in 30 s\n", stage);
    fflush(stdout);
    TerminateProcess(GetCurrentProcess(), 1);  // ExitProcess would wait for the stuck thread's locks
  }
  GetExitCodeThread(thread, &vk);
  return (int)vk;
}

static int d3d11(void) {
  IDXGIFactory1 *factory;
  IDXGIAdapter1 *adapter;
  DXGI_ADAPTER_DESC1 desc;
  CHECK(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **)&factory));
  CHECK(IDXGIFactory1_EnumAdapters1(factory, 0, &adapter));
  CHECK(IDXGIAdapter1_GetDesc1(adapter, &desc));
  printf("adapter %ls vendor %04x device %04x\n", desc.Description, desc.VendorId, desc.DeviceId);

  ID3D11Device *device;
  ID3D11DeviceContext *context;
  D3D_FEATURE_LEVEL level;
  CHECK(D3D11CreateDevice((IDXGIAdapter *)adapter, D3D_DRIVER_TYPE_UNKNOWN, NULL, 0, NULL, 0, D3D11_SDK_VERSION, &device,
                          &level, &context));
  printf("feature level %x\n", (unsigned)level);

  Compile compile = (Compile)(void *)GetProcAddress(LoadLibraryA("d3dcompiler_47.dll"), "D3DCompile");
  if (!compile) {
    printf("FAIL no d3dcompiler_47\n");
    return 1;
  }
  ID3DBlob *vsCode, *psCode, *errors = NULL;
  CHECK(compile(source, strlen(source), NULL, NULL, NULL, "vs", "vs_4_0", 0, 0, &vsCode, &errors));
  CHECK(compile(source, strlen(source), NULL, NULL, NULL, "ps", "ps_4_0", 0, 0, &psCode, &errors));
  ID3D11VertexShader *vs;
  ID3D11PixelShader *ps;
  CHECK(ID3D11Device_CreateVertexShader(device, ID3D10Blob_GetBufferPointer(vsCode), ID3D10Blob_GetBufferSize(vsCode),
                                        NULL, &vs));
  CHECK(ID3D11Device_CreatePixelShader(device, ID3D10Blob_GetBufferPointer(psCode), ID3D10Blob_GetBufferSize(psCode),
                                       NULL, &ps));

  D3D11_TEXTURE2D_DESC texture = {256, 256, 1, 1, DXGI_FORMAT_R8G8B8A8_UNORM, {1, 0}, D3D11_USAGE_DEFAULT,
                                  D3D11_BIND_RENDER_TARGET, 0, 0};
  ID3D11Texture2D *target, *staging;
  CHECK(ID3D11Device_CreateTexture2D(device, &texture, NULL, &target));
  texture.Usage = D3D11_USAGE_STAGING;
  texture.BindFlags = 0;
  texture.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  CHECK(ID3D11Device_CreateTexture2D(device, &texture, NULL, &staging));
  ID3D11RenderTargetView *view;
  CHECK(ID3D11Device_CreateRenderTargetView(device, (ID3D11Resource *)target, NULL, &view));

  const D3D11_VIEWPORT viewport = {0, 0, 256, 256, 0, 1};
  const float blue[4] = {0, 0, 1, 1};
  ID3D11DeviceContext_OMSetRenderTargets(context, 1, &view, NULL);
  ID3D11DeviceContext_RSSetViewports(context, 1, &viewport);
  ID3D11DeviceContext_ClearRenderTargetView(context, view, blue);
  ID3D11DeviceContext_IASetPrimitiveTopology(context, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
  ID3D11DeviceContext_VSSetShader(context, vs, NULL, 0);
  ID3D11DeviceContext_PSSetShader(context, ps, NULL, 0);
  ID3D11DeviceContext_Draw(context, 3, 0);
  ID3D11DeviceContext_CopyResource(context, (ID3D11Resource *)staging, (ID3D11Resource *)target);
  D3D11_MAPPED_SUBRESOURCE map;
  CHECK(ID3D11DeviceContext_Map(context, (ID3D11Resource *)staging, 0, D3D11_MAP_READ, 0, &map));
  const unsigned char *center = (const unsigned char *)map.pData + 128 * map.RowPitch + 128 * 4;
  const unsigned char *corner = (const unsigned char *)map.pData + 4 * map.RowPitch + 4 * 4;
  const int ok = center[0] == 255 && center[1] >= 127 && center[1] <= 128 && center[2] == 0 && corner[0] == 0 &&
                 corner[2] == 255;
  printf("%s: center %u,%u,%u corner %u,%u,%u\n", ok ? "PASS triangle" : "FAIL pixels", center[0], center[1],
         center[2], corner[0], corner[1], corner[2]);
  return ok ? 0 : 1;
}

/* --foreground EXE: waits up to 90 s for the focused window to belong to EXE (a fresh install's
 * first sign-in can take a minute to draw an application's window), then waits for it to take input. */
static int foreground(const char *exe) {
  for (int i = 0; i < 180; ++i, Sleep(500)) {
    HWND window = GetForegroundWindow();
    DWORD pid = 0;
    if (!window || !GetWindowThreadProcessId(window, &pid) || !IsWindowVisible(window))
      continue;
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    char path[MAX_PATH];
    DWORD size = sizeof path;
    const BOOL named = process && QueryFullProcessImageNameA(process, 0, path, &size);
    if (process)
      CloseHandle(process);
    const char *name = named ? strrchr(path, '\\') : NULL;
    if (name && !_stricmp(name + 1, exe)) {
      DWORD result;
      SendMessageTimeoutA(window, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, 30000, (PDWORD_PTR)&result);
      printf("PASS foreground %s after %.1f s\n", exe, i * 0.5);
      return 0;
    }
  }
  printf("FAIL foreground: %s has no focused window after 90 s\n", exe);
  return 1;
}

int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IONBF, 0);
  const char *mode = argc > 1 ? argv[1] : "";
  if (!strcmp(mode, "--foreground") && argc > 2)
    return foreground(argv[2]);
  if (!strcmp(mode, "--vulkan"))
    return vulkan_timed();
  const int d3d = d3d11();
  if (d3d || strcmp(mode, "--d3d-vulkan"))
    return d3d;
  return vulkan_timed();
}
