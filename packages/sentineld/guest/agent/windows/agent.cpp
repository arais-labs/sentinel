// sentineld-agent for Windows guests (DESIGN.md §9–11): the Linux agent's protocol on Win32. vsock
// port 1 through virtio-win's viosock Winsock provider, one operation per connection; frames are
// [u8 type][u32 BE len][bytes], the first one the JSON request. Started at boot as SYSTEM from the
// share disk (distributions/windows-11/install), so a new agent needs no reinstall. Desktop work
// (typing, the clipboard, the wallpaper) runs in the signed-in user's session: the agent starts
// itself there as a helper, `sentineld-agent.exe desktop <op>` (below).
#include <winsock2.h>
#include <windows.h>
#include <userenv.h>
#include <wtsapi32.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "../../../core/util/json.hpp"

namespace {

using namespace sentineld;

enum Frame : uint8_t { Stdin = 0, Output = 1, Resize = 2, Exit = 3, Request = 4, Started = 5, Stderr = 6 };
constexpr uint32_t kMaxFrame = 1 << 20;
constexpr uint32_t kPort = 1;

// viosock's address (vio_sockets.h): its family is the provider's, asked of the device.
struct SockaddrVm {
  ADDRESS_FAMILY family;
  USHORT reserved;
  UINT port;
  UINT cid;
};
constexpr UINT kCidAny = UINT(-1);
constexpr DWORD kIoctlGetAf = 0x0801300C;

ADDRESS_FAMILY vsockFamily() {
  DWORD af = AF_UNSPEC, got = 0;
  HANDLE device = CreateFileW(L"\\??\\Viosock", GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
  if (device == INVALID_HANDLE_VALUE) return AF_UNSPEC;
  if (!DeviceIoControl(device, kIoctlGetAf, nullptr, 0, &af, sizeof af, &got, nullptr)) af = AF_UNSPEC;
  CloseHandle(device);
  return ADDRESS_FAMILY(af);
}

// One connection: frames out are serialized (the output readers and the exit frame share it).
struct Connection {
  SOCKET s;
  std::mutex write;

  bool readAll(void* buf, size_t n) {
    auto* p = static_cast<char*>(buf);
    while (n) {
      int r = recv(s, p, int(std::min<size_t>(n, 1 << 20)), 0);
      if (r < 0 && WSAGetLastError() == WSAETIMEDOUT) continue;  // the host's close is seen by the next receive
      if (r <= 0) return false;
      p += r, n -= size_t(r);
    }
    return true;
  }
  bool writeAll(const void* buf, size_t n) {
    auto* p = static_cast<const char*>(buf);
    while (n) {
      int w = ::send(s, p, int(std::min<size_t>(n, 1 << 20)), 0);
      if (w <= 0) return false;
      p += w, n -= size_t(w);
    }
    return true;
  }
  bool send(uint8_t type, const void* data, size_t n) {
    std::lock_guard<std::mutex> lock(write);
    uint8_t h[5] = {type, uint8_t(n >> 24), uint8_t(n >> 16), uint8_t(n >> 8), uint8_t(n)};
    return writeAll(h, 5) && writeAll(data, n);
  }
  bool send(uint8_t type, const std::string& s) { return send(type, s.data(), s.size()); }
  bool receive(uint8_t& type, std::string& payload) {
    uint8_t h[5];
    if (!readAll(h, 5)) return false;
    uint32_t n = uint32_t(h[1]) << 24 | uint32_t(h[2]) << 16 | uint32_t(h[3]) << 8 | h[4];
    if (n > kMaxFrame) return false;
    type = h[0];
    payload.resize(n);
    return readAll(payload.data(), n);
  }
  void exit(json::Object o) { send(Exit, json::dump(o)); }
};

std::wstring wide(const std::string& s) {
  if (s.empty()) return {};
  std::wstring w(size_t(MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0)), L'\0');
  MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), w.data(), int(w.size()));
  return w;
}

std::string narrow(const std::wstring& w) {
  if (w.empty()) return {};
  std::string s(size_t(WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), nullptr, 0, nullptr, nullptr)), '\0');
  WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), s.data(), int(s.size()), nullptr, nullptr);
  return s;
}

std::string tail(const std::string& s, size_t n = 400) { return s.substr(s.size() - std::min(s.size(), n)); }

// Enables one of the agent's (SYSTEM's) privileges, which its token holds disabled.
void enable(const wchar_t* privilege) {
  HANDLE token;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &token)) return;
  TOKEN_PRIVILEGES p{1, {{{}, SE_PRIVILEGE_ENABLED}}};
  if (LookupPrivilegeValueW(nullptr, privilege, &p.Privileges[0].Luid)) AdjustTokenPrivileges(token, FALSE, &p, 0, nullptr, nullptr);
  CloseHandle(token);
}

// One argument quoted the way CommandLineToArgvW and the C runtime read it back.
std::wstring quoted(const std::wstring& arg) {
  if (!arg.empty() && arg.find_first_of(L" \t\n\v\"") == std::wstring::npos) return arg;
  std::wstring out = L"\"";
  for (size_t i = 0;; ++i) {
    size_t backslashes = 0;
    while (i < arg.size() && arg[i] == L'\\') ++i, ++backslashes;
    if (i == arg.size()) {
      out.append(backslashes * 2, L'\\');
      break;
    }
    if (arg[i] == L'"') out.append(backslashes * 2 + 1, L'\\').push_back(L'"');
    else out.append(backslashes, L'\\').push_back(arg[i]);
  }
  return out + L"\"";
}

// --- the POSIX userland --------------------------------------------------------------------
// What the guest's distribution folder (the share disk's \distribution, DESIGN.md §17) says the agent's
// commands run with (§9): its `env`, values with %VARIABLES% expanded (Windows 11: MSYS2's bin first
// on PATH, HOME=/root), set on the agent itself so every command inherits it, and SENTINEL_SHARE, the
// share's root, for commands that name its files. Read once at start: the share is the same until the
// next boot.

void environment() {
  wchar_t path[MAX_PATH];
  const DWORD n = GetModuleFileNameW(nullptr, path, MAX_PATH);
  if (n == 0 || n == MAX_PATH) return;
  std::wstring share(path, n);
  share.resize(share.find_last_of(L'\\'));  // <share>\windows
  share.resize(share.find_last_of(L'\\'));
  SetEnvironmentVariableW(L"SENTINEL_SHARE", share.c_str());
  HANDLE f = CreateFileW((share + L"\\distribution\\distribution.json").c_str(), GENERIC_READ,
                         FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
  if (f == INVALID_HANDLE_VALUE) return;
  std::string text;
  char buf[65536];
  DWORD got;
  while (ReadFile(f, buf, sizeof buf, &got, nullptr) && got) text.append(buf, got);
  CloseHandle(f);
  try {
    const json::Value d = json::parse(text);
    if (auto* env = d["env"].obj())
      for (auto& [name, value] : *env) {
        const std::wstring raw = wide(value.str());
        std::wstring expanded(ExpandEnvironmentStringsW(raw.c_str(), nullptr, 0), L'\0');
        ExpandEnvironmentStringsW(raw.c_str(), expanded.data(), DWORD(expanded.size()));
        expanded.resize(wcslen(expanded.c_str()));
        SetEnvironmentVariableW(wide(name).c_str(), expanded.c_str());
      }
  } catch (const std::exception& e) {
    std::fprintf(stderr, "sentineld-agent: distribution.json: %s\n", e.what());
  }
}

// The program a command names, found on PATH alone, in its order, as a POSIX shell finds it: Windows'
// own search would take its system folders first (System32's tar, curl or WSL's bash over the
// userland's). A name with a folder is the program itself (empty: CreateProcess takes it as given).
std::wstring program(const std::wstring& name) {
  if (name.find_first_of(L"\\/:") != std::wstring::npos) return {};
  std::wstring path(GetEnvironmentVariableW(L"PATH", nullptr, 0), L'\0');
  path.resize(GetEnvironmentVariableW(L"PATH", path.data(), DWORD(path.size())));
  wchar_t found[MAX_PATH];
  const DWORD n = SearchPathW(path.c_str(), name.c_str(), L".exe", MAX_PATH, found, nullptr);
  return n && n < MAX_PATH ? std::wstring(found, n) : std::wstring();
}

// Whether a program is an MSYS2 (Cygwin) one: its runtime, which the loader takes from the program's
// own folder, reads the command line itself. With the userland's MSYS=noglob (distribution.json `env`;
// its glob mode cut every quoted argument at 8186 characters) it reads it as a POSIX shell's words do:
// '...' and "..." quote up to the next same quote, no escapes, adjacent pieces join.
bool cygwin(const std::wstring& program) {
  const size_t slash = program.find_last_of(L"\\/");
  if (slash == std::wstring::npos) return false;
  const std::wstring folder = program.substr(0, slash + 1);
  return GetFileAttributesW((folder + L"msys-2.0.dll").c_str()) != INVALID_FILE_ATTRIBUTES ||
         GetFileAttributesW((folder + L"cygwin1.dll").c_str()) != INVALID_FILE_ATTRIBUTES;
}

// One argument as such a runtime reads it back: double-quoted pieces, a double quote in single quotes.
std::wstring quotedPosix(const std::wstring& arg) {
  const auto plain = [](wchar_t ch) { return iswalnum(ch) || std::wcschr(L"_-./:=,+@%", ch); };
  if (!arg.empty() && std::all_of(arg.begin(), arg.end(), plain)) return arg;
  std::wstring out;
  for (size_t at = 0;;) {
    const size_t quote = arg.find(L'"', at);
    if (quote != at || arg.empty()) out += L"\"" + arg.substr(at, quote - at) + L"\"";
    if (quote == std::wstring::npos) return out;
    out += L"'\"'";
    at = quote + 1;
    if (at == arg.size()) return out;
  }
}

void status(Connection& c) {
  c.send(Output, json::dump(json::Object{{"ready", true}}));
  c.exit({{"code", 0}});
}

// Runs argv with pipes (no terminal: terminals are tmux's, whose panes have the POSIX userland's own
// terminals); stdout and stderr stream back as frames, stdin frames go to the process. The process
// and what it starts run in a job, killed when the connection ends first (sentineld's time limit, a
// closed process connection). What it leaves running once it exited stays: Windows has no sessions to
// leave, and a daemon (tmux's server) outlives the command that started it, as on Linux.
void run(Connection& c, const json::Value& req) {
  if (req["argv"].arr().empty()) return c.exit({{"error", "argv is empty"}});
  const std::wstring name = wide(req["argv"].arr()[0].str()), exe = program(name);
  const bool posix = cygwin(exe.empty() ? name : exe);
  std::wstring command;
  for (auto& a : req["argv"].arr()) command += (command.empty() ? L"" : L" ") + (posix ? quotedPosix : quoted)(wide(a.str()));
  const bool split = !req["stderr"].isNull() && req["stderr"].str() == "split";
  const std::wstring cwd = req["cwd"].isNull() ? L"C:\\" : wide(req["cwd"].str());

  SECURITY_ATTRIBUTES inherit{sizeof inherit, nullptr, TRUE};
  HANDLE inRead, inWrite, outRead, outWrite, errRead = nullptr, errWrite = nullptr;
  if (!CreatePipe(&inRead, &inWrite, &inherit, 0) || !CreatePipe(&outRead, &outWrite, &inherit, 0) ||
      (split && !CreatePipe(&errRead, &errWrite, &inherit, 0)))
    return c.exit({{"error", "cannot create pipes"}});
  SetHandleInformation(inWrite, HANDLE_FLAG_INHERIT, 0);
  SetHandleInformation(outRead, HANDLE_FLAG_INHERIT, 0);
  if (split) SetHandleInformation(errRead, HANDLE_FLAG_INHERIT, 0);

  STARTUPINFOW si{};
  si.cb = sizeof si;
  si.dwFlags = STARTF_USESTDHANDLES;
  si.hStdInput = inRead;
  si.hStdOutput = outWrite;
  si.hStdError = split ? errWrite : outWrite;
  PROCESS_INFORMATION pi{};
  HANDLE job = CreateJobObjectW(nullptr, nullptr);
  std::vector<wchar_t> line(command.begin(), command.end());
  line.push_back(L'\0');
  const BOOL started = CreateProcessW(exe.empty() ? nullptr : exe.c_str(), line.data(), nullptr, nullptr, TRUE,
                                      CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, nullptr,
                                      cwd.c_str(), &si, &pi);
  const DWORD startError = started ? 0 : GetLastError();
  CloseHandle(inRead);
  CloseHandle(outWrite);
  if (split) CloseHandle(errWrite);
  if (!started) {
    CloseHandle(inWrite), CloseHandle(outRead), CloseHandle(job);
    if (split) CloseHandle(errRead);
    // Like a shell: exit status 127, the reason on the program's error output.
    const std::string why = "cannot start " + req["argv"].arr()[0].str() + " (error " + std::to_string(startError) + ")\n";
    c.send(split ? Stderr : Output, why);
    return c.exit({{"code", 127}});
  }
  AssignProcessToJobObject(job, pi.hProcess);
  ResumeThread(pi.hThread);
  CloseHandle(pi.hThread);
  c.send(Started, "", 0);

  std::atomic<bool> alive{true};
  auto pump = [&c, &alive](HANDLE from, uint8_t type) {
    char buf[64 * 1024];
    DWORD n;
    while (ReadFile(from, buf, sizeof buf, &n, nullptr) && n)
      if (alive) c.send(type, buf, n);
  };
  std::thread out(pump, outRead, uint8_t(Output));
  std::thread err;
  if (split) err = std::thread(pump, errRead, uint8_t(Stderr));
  // sentineld's frames: stdin, its end (an empty frame); the connection closing stops the run.
  std::thread input([&c, &alive, &inWrite, job] {
    uint8_t type;
    std::string data;
    while (c.receive(type, data)) {
      if (type == Stdin && data.empty()) {
        CloseHandle(std::exchange(inWrite, nullptr));
      } else if (type == Stdin && inWrite) {
        DWORD w;
        WriteFile(inWrite, data.data(), DWORD(data.size()), &w, nullptr);
      } else if (type != Resize) {
        break;
      }
    }
    if (alive.exchange(false)) TerminateJobObject(job, 1);
  });

  WaitForSingleObject(pi.hProcess, INFINITE);
  DWORD code = 0;
  GetExitCodeProcess(pi.hProcess, &code);
  out.join();  // the pipe ends once nothing in the job holds it
  if (err.joinable()) err.join();
  if (alive.exchange(false)) c.exit({{"code", uint64_t(code)}});
  shutdown(c.s, SD_RECEIVE);  // ends the input thread's receive; the socket closes after the operation
  input.join();
  CloseHandle(pi.hProcess), CloseHandle(outRead), CloseHandle(job);
  if (inWrite) CloseHandle(inWrite);
  if (split) CloseHandle(errRead);
}

// Starts Windows' own shutdown (it returns at once; the guest powers off when done) and only then
// replies: a shutdown Windows refused is an error, so sentineld powers off now instead of waiting
// out the distribution's grace period for nothing.
void powerOff(Connection& c) {
  enable(L"SeShutdownPrivilege");
  const DWORD err = InitiateShutdownW(nullptr, nullptr, 0, SHUTDOWN_POWEROFF | SHUTDOWN_FORCE_OTHERS | SHUTDOWN_FORCE_SELF,
                                      SHTDN_REASON_FLAG_PLANNED);
  if (err == ERROR_SUCCESS || err == ERROR_SHUTDOWN_IN_PROGRESS) return c.exit({{"code", 0}});
  c.exit({{"error", "InitiateShutdown failed (error " + std::to_string(err) + ")"}});
}

// --- the desktop ------------------------------------------------------------------------
// Typing for computer use, the clipboard and the wallpaper (DESIGN.md §8, §22) act on the signed-in
// user's desktop, which only a process in that user's session reaches (SendInput, the clipboard,
// SystemParametersInfo), not the agent in session 0. So the agent runs its own executable there,
// `sentineld-agent.exe desktop <op> [arg]`: text on its stdin and stdout, why it failed on stderr.

constexpr size_t kClipboardMax = 256 * 1024;

struct Owned {
  HANDLE h = nullptr;
  Owned() = default;
  Owned(const Owned&) = delete;
  Owned& operator=(const Owned&) = delete;
  ~Owned() { close(); }
  void close() {
    if (h && h != INVALID_HANDLE_VALUE) CloseHandle(h);
    h = nullptr;
  }
};

// The desktop helper with `args`, in the console session as its user, elevated when the user is an
// administrator (UAC's linked token: input then reaches elevated windows too), on the interactive
// desktop. `input` on its stdin; its stdout into `out`, its stderr into `err` (each up to 1 MiB).
// Stopped after `seconds`. Its exit code, or -1 (why in `err`) when it could not start.
int inSession(const std::wstring& args, const std::string& input, std::string& out, std::string& err, DWORD seconds) {
  Owned token;
  const DWORD session = WTSGetActiveConsoleSessionId();
  if (session == 0xFFFFFFFF || !WTSQueryUserToken(session, &token.h)) {
    err = "no one is signed in to the desktop yet";
    return -1;
  }
  TOKEN_ELEVATION_TYPE elevation;
  TOKEN_LINKED_TOKEN linked{};
  DWORD n;
  if (GetTokenInformation(token.h, TokenElevationType, &elevation, sizeof elevation, &n) && elevation == TokenElevationTypeLimited &&
      GetTokenInformation(token.h, TokenLinkedToken, &linked, sizeof linked, &n)) {
    token.close();
    token.h = linked.LinkedToken;
  }
  wchar_t exe[MAX_PATH];
  if (!GetModuleFileNameW(nullptr, exe, MAX_PATH)) return err = "cannot find the agent's executable", -1;
  std::wstring line = quoted(exe) + L" desktop " + args;

  SECURITY_ATTRIBUTES inherit{sizeof inherit, nullptr, TRUE};
  Owned inRead, inWrite, outRead, outWrite, errRead, errWrite, job, process;
  if (!CreatePipe(&inRead.h, &inWrite.h, &inherit, 0) || !CreatePipe(&outRead.h, &outWrite.h, &inherit, 0) ||
      !CreatePipe(&errRead.h, &errWrite.h, &inherit, 0))
    return err = "cannot create pipes", -1;
  for (HANDLE ours : {inWrite.h, outRead.h, errRead.h}) SetHandleInformation(ours, HANDLE_FLAG_INHERIT, 0);
  void* environment = nullptr;
  CreateEnvironmentBlock(&environment, token.h, FALSE);  // the user's own (LOCALAPPDATA, ...)
  STARTUPINFOW si{};
  si.cb = sizeof si;
  si.lpDesktop = const_cast<wchar_t*>(L"winsta0\\default");
  si.dwFlags = STARTF_USESTDHANDLES;
  si.hStdInput = inRead.h;
  si.hStdOutput = outWrite.h;
  si.hStdError = errWrite.h;
  PROCESS_INFORMATION pi{};
  job.h = CreateJobObjectW(nullptr, nullptr);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  SetInformationJobObject(job.h, JobObjectExtendedLimitInformation, &limits, sizeof limits);
  const BOOL started = CreateProcessAsUserW(token.h, exe, line.data(), nullptr, nullptr, TRUE,
                                            CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, environment,
                                            nullptr, &si, &pi);
  const DWORD startError = started ? 0 : GetLastError();
  if (environment) DestroyEnvironmentBlock(environment);
  inRead.close(), outWrite.close(), errWrite.close();
  if (!started) return err = "cannot start the desktop helper (error " + std::to_string(startError) + ")", -1;
  process.h = pi.hProcess;
  AssignProcessToJobObject(job.h, pi.hProcess);
  ResumeThread(pi.hThread);
  CloseHandle(pi.hThread);

  auto drain = [](HANDLE from, std::string& to) {
    char buf[4096];
    DWORD got;
    while (ReadFile(from, buf, sizeof buf, &got, nullptr) && got)
      if (to.size() < (1u << 20)) to.append(buf, got);
  };
  std::string errors;
  std::thread writer([&] {
    DWORD written;
    if (!input.empty()) WriteFile(inWrite.h, input.data(), DWORD(input.size()), &written, nullptr);
    inWrite.close();
  });
  std::thread outReader(drain, outRead.h, std::ref(out)), errReader(drain, errRead.h, std::ref(errors));
  const bool done = WaitForSingleObject(process.h, seconds * 1000) == WAIT_OBJECT_0;
  if (!done) TerminateJobObject(job.h, 124);
  writer.join(), outReader.join(), errReader.join();
  DWORD code = 124;
  if (done) GetExitCodeProcess(process.h, &code);
  err = done ? errors : errors + "(timed out after " + std::to_string(seconds) + " s)";
  return int(code);
}

void typeText(Connection& c, const json::Value& req) {
  const std::string text = req["text"].isNull() ? "" : req["text"].str();
  if (text.size() > 4000 || text.find('\0') != std::string::npos)
    return c.exit({{"error", "text must be at most 4000 bytes without NUL"}});
  std::string out, err;
  if (int code = inSession(L"type", text, out, err, 20); code != 0)
    return c.exit({{"error", "typing failed" + (code > 0 ? " (exit " + std::to_string(code) + ")" : "") + ": " + tail(err)}});
  c.exit({{"code", 0}});
}

void clipboard(Connection& c, const json::Value& req) {
  const std::string action = req["action"].isNull() ? "" : req["action"].str();
  if (action != "read" && action != "write") return c.exit({{"error", "clipboard action must be read or write"}});
  const std::string text = action == "write" && !req["text"].isNull() ? req["text"].str() : "";
  if (text.size() > kClipboardMax || text.find('\0') != std::string::npos)
    return c.exit({{"error", "clipboard text must be at most 256 KiB without NUL"}});
  std::string out, err;
  if (int code = inSession(L"clipboard " + wide(action), text, out, err, 10); code != 0)
    return c.exit({{"error", "the clipboard " + action + " failed" + (code > 0 ? " (exit " + std::to_string(code) + ")" : "") + ": " + tail(err)}});
  if (out.size() > kClipboardMax) return c.exit({{"error", "clipboard text exceeds 256 KiB"}});
  c.send(Output, json::dump(action == "read" ? json::Object{{"text", out}} : json::Object{}));
  c.exit({{"code", 0}});
}

void wallpaper(Connection& c, const json::Value& req) {
  const std::string design = req["design"].isNull() ? "" : req["design"].str();
  if (design.empty() || design.size() > 64 ||
      !std::all_of(design.begin(), design.end(), [](char ch) { return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-'; }))
    return c.exit({{"error", "invalid wallpaper design"}});
  std::string out, err;
  if (int code = inSession(L"wallpaper " + wide(design), "", out, err, 30); code != 0)
    return c.exit({{"error", "the wallpaper could not be applied" + (code > 0 ? " (exit " + std::to_string(code) + ")" : "") + ": " + tail(err)}});
  c.exit({{"code", 0}});
}

// --- the desktop helper, in the user's session ------------------------------------------

std::string readInput() {
  std::string s;
  char buf[4096];
  DWORD n;
  while (ReadFile(GetStdHandle(STD_INPUT_HANDLE), buf, sizeof buf, &n, nullptr) && n) s.append(buf, n);
  return s;
}

void writeTo(DWORD stream, const std::string& s) {
  DWORD n;
  WriteFile(GetStdHandle(stream), s.data(), DWORD(s.size()), &n, nullptr);
}

int fail(const std::string& why) {
  writeTo(STD_ERROR_HANDLE, why);
  return 1;
}

// Text as Unicode key events (VK_PACKET), whatever the keyboard layout; line breaks and tabs as their
// keys, which applications take as such (a packet's "\n" is no Return to most).
int typeKeys(const std::wstring& text) {
  std::vector<INPUT> events;
  auto key = [&events](WORD vk, WORD scan, DWORD flags) {
    for (DWORD up : {DWORD(0), DWORD(KEYEVENTF_KEYUP)}) {
      INPUT in{};
      in.type = INPUT_KEYBOARD;
      in.ki.wVk = vk;
      in.ki.wScan = scan;
      in.ki.dwFlags = flags | up;
      events.push_back(in);
    }
  };
  for (size_t i = 0; i < text.size(); ++i) {
    const wchar_t ch = text[i];
    if (ch == L'\r' && i + 1 < text.size() && text[i + 1] == L'\n') continue;
    if (ch == L'\n' || ch == L'\r' || ch == L'\t') {
      const WORD vk = ch == L'\t' ? VK_TAB : VK_RETURN;
      key(vk, WORD(MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)), 0);
    } else {
      key(0, WORD(ch), KEYEVENTF_UNICODE);  // a surrogate pair is two packets, which applications join
    }
  }
  // A chord computer use just pressed is released through the virtual keyboard, a path of its own
  // that SendInput can overtake: text typed while Control still reads as held is lost (as Control+!).
  // So the modifiers are up first (at most 1 s).
  for (int wait = 0; wait < 100; ++wait, Sleep(10)) {
    bool held = false;
    for (int vk : {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN}) held = held || (GetAsyncKeyState(vk) & 0x8000);
    if (!held) break;
  }
  // In slices: an application drains its input at its own pace.
  for (size_t at = 0; at < events.size(); at += 64) {
    const UINT n = UINT(std::min<size_t>(64, events.size() - at));
    if (SendInput(n, events.data() + at, sizeof(INPUT)) != n)
      return fail("the desktop takes no input (locked, or a secure desktop is showing)");
    Sleep(8);
  }
  return 0;
}

bool openClipboard(HWND owner) {
  for (int attempt = 0; attempt < 50; ++attempt, Sleep(20))  // another application may hold it a moment
    if (OpenClipboard(owner)) return true;
  return false;
}

// Text in and out as UTF-8 with "\n" line breaks; the clipboard's has "\r\n", Windows' convention.
int clipboardRead() {
  if (!openClipboard(nullptr)) return fail("the clipboard is in use");
  std::wstring text;
  if (HANDLE data = GetClipboardData(CF_UNICODETEXT))
    if (auto* p = static_cast<const wchar_t*>(GlobalLock(data))) {
      text.assign(p, wcsnlen(p, GlobalSize(data) / sizeof(wchar_t)));
      GlobalUnlock(data);
    }
  CloseClipboard();
  std::wstring lf;
  for (size_t i = 0; i < text.size(); ++i)
    if (!(text[i] == L'\r' && i + 1 < text.size() && text[i + 1] == L'\n')) lf += text[i];
  writeTo(STD_OUTPUT_HANDLE, narrow(lf));
  return 0;
}

int clipboardWrite(const std::wstring& text) {
  std::wstring crlf;
  for (size_t i = 0; i < text.size(); ++i) {
    if (text[i] == L'\n' && (i == 0 || text[i - 1] != L'\r')) crlf += L'\r';
    crlf += text[i];
  }
  // An owner window: EmptyClipboard with none leaves the clipboard ownerless, and SetClipboardData
  // then fails. What it set stays on the clipboard after the helper exits.
  HWND owner = CreateWindowExW(0, L"STATIC", nullptr, 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, nullptr, nullptr);
  HGLOBAL data = GlobalAlloc(GMEM_MOVEABLE, (crlf.size() + 1) * sizeof(wchar_t));
  if (!owner || !data) return fail("out of memory");
  std::copy_n(crlf.c_str(), crlf.size() + 1, static_cast<wchar_t*>(GlobalLock(data)));
  GlobalUnlock(data);
  if (!openClipboard(owner)) return GlobalFree(data), fail("the clipboard is in use");
  const bool set = EmptyClipboard() && SetClipboardData(CF_UNICODETEXT, data);
  CloseClipboard();
  DestroyWindow(owner);
  if (!set) return GlobalFree(data), fail("the clipboard did not take the text (error " + std::to_string(GetLastError()) + ")");
  return 0;
}

// One of the share's wallpapers (<share>\wallpapers\<design>.png, beside <share>\windows\ that holds
// this executable), copied into the user's local application data and set filling the screen. Named by
// its size too, as on Linux: a changed image gets a new path, which Windows does not show cached.
int setWallpaper(const std::wstring& design) {
  wchar_t exe[MAX_PATH];
  std::wstring share(exe, GetModuleFileNameW(nullptr, exe, MAX_PATH));
  for (int up = 0; up < 2 && share.find_last_of(L'\\') != std::wstring::npos; ++up) share.resize(share.find_last_of(L'\\'));
  const std::wstring image = share + L"\\wallpapers\\" + design + L".png";
  WIN32_FILE_ATTRIBUTE_DATA info;
  if (!GetFileAttributesExW(image.c_str(), GetFileExInfoStandard, &info)) return fail("no wallpaper " + narrow(design));
  wchar_t local[MAX_PATH];
  const DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
  if (!n || n >= MAX_PATH) return fail("the user has no local application data folder");
  std::wstring dir = std::wstring(local) + L"\\Sentinel";
  CreateDirectoryW(dir.c_str(), nullptr);
  dir += L"\\Wallpapers";
  CreateDirectoryW(dir.c_str(), nullptr);
  std::wstring file = dir + L"\\" + design + L"-" + std::to_wstring(info.nFileSizeLow) + L".png";
  if (!CopyFileW(image.c_str(), file.c_str(), FALSE))
    return fail("the wallpaper could not be saved (error " + std::to_string(GetLastError()) + ")");
  for (auto [name, value] : {std::pair{L"WallpaperStyle", L"10"}, {L"TileWallpaper", L"0"}})  // Fill
    RegSetKeyValueW(HKEY_CURRENT_USER, L"Control Panel\\Desktop", name, REG_SZ, value, DWORD((wcslen(value) + 1) * sizeof(wchar_t)));
  if (!SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, file.data(), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE))
    return fail("Windows did not take the wallpaper (error " + std::to_string(GetLastError()) + ")");
  return 0;
}

int desktop(int argc, char** argv) {
  const std::string op = argv[2], arg = argc > 3 ? argv[3] : "";
  if (op == "type") return typeKeys(wide(readInput()));
  if (op == "clipboard" && arg == "read") return clipboardRead();
  if (op == "clipboard" && arg == "write") return clipboardWrite(wide(readInput()));
  if (op == "wallpaper" && !arg.empty()) return setWallpaper(wide(arg));
  return fail("unknown desktop operation " + op);
}

void connection(SOCKET s) {
  // viosock never completes a pending receive when the host resets the connection (sentineld's time
  // limit, a closed process connection): the run would live on unseen. A receive that started after
  // the reset fails, so receives wait at most a second each, and the next one sees it.
  const DWORD wait = 1000;
  setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&wait), sizeof wait);
  Connection c{s, {}};
  uint8_t type;
  std::string payload;
  if (c.receive(type, payload) && type == Request) {
    try {
      json::Value req = json::parse(payload);
      const std::string& op = req["op"].str();
      if (op == "status") status(c);
      else if (op == "run") run(c, req);
      else if (op == "shutdown") powerOff(c);
      else if (op == "type") typeText(c, req);
      else if (op == "clipboard") clipboard(c, req);
      else if (op == "wallpaper") wallpaper(c, req);
      else c.exit({{"error", "unsupported on Windows: " + op}});
    } catch (const std::exception& e) {
      c.exit({{"error", e.what()}});
    }
  }
  if (c.s != INVALID_SOCKET) closesocket(c.s);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc > 2 && std::string(argv[1]) == "desktop") return desktop(argc, argv);
  environment();
  // The desktop helper's start: the signed-in user's token (TCB), a process made with it.
  for (const wchar_t* privilege : {L"SeTcbPrivilege", L"SeAssignPrimaryTokenPrivilege", L"SeIncreaseQuotaPrivilege"})
    enable(privilege);
  WSADATA wsa;
  if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 1;
  // The vsock driver and its Winsock provider come up with the device: wait for them at boot.
  ADDRESS_FAMILY af = AF_UNSPEC;
  SOCKET s = INVALID_SOCKET;
  SockaddrVm addr{};
  for (int attempt = 0; attempt < 600; ++attempt, Sleep(1000)) {
    if ((af = vsockFamily()) == AF_UNSPEC) continue;
    if ((s = socket(af, SOCK_STREAM, 0)) == INVALID_SOCKET) continue;
    addr = {af, 0, kPort, kCidAny};
    if (bind(s, reinterpret_cast<sockaddr*>(&addr), sizeof addr) == 0 && listen(s, 64) == 0) break;
    closesocket(std::exchange(s, INVALID_SOCKET));
  }
  if (s == INVALID_SOCKET) {
    std::fprintf(stderr, "sentineld-agent: no vsock (error %d)\n", WSAGetLastError());
    return 1;
  }
  for (;;) {
    SOCKET c = accept(s, nullptr, nullptr);
    if (c == INVALID_SOCKET) continue;
    std::thread(connection, c).detach();
  }
}
