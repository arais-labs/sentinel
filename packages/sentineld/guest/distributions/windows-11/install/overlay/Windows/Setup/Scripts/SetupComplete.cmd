@echo off
rem Sentinel's end of Windows setup (Windows runs Setup\Scripts\SetupComplete.cmd once, as SYSTEM,
rem when setup completes): the agent's boot task, a machine that stays on (a sleeping or hibernating
rem guest powers its VM off) with its clock in UTC like the host's, then the report to sentineld on
rem the EFI system partition (sentinel\result: "ok" or why it failed). The virtio drivers other than the
rem disk's (installed offline) install here; the power-off that ends the install leaves the first logon
rem a minute to finish, so workspaces start from a desktop.
mountvol S: /s
mkdir S:\sentinel 2>nul
set failed=
pnputil /add-driver C:\Windows\Drivers\VirtIO\*.inf /subdirs /install >> S:\sentinel\log 2>&1
schtasks /create /tn "Sentinel agent" /xml C:\ProgramData\Sentinel\agent-task.xml /f >> S:\sentinel\log 2>&1 || set failed=registering the agent task failed
powercfg /hibernate off >> S:\sentinel\log 2>&1
for %%s in (standby-timeout-ac monitor-timeout-ac disk-timeout-ac hibernate-timeout-ac) do powercfg /change %%s 0 >> S:\sentinel\log 2>&1
reg add HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation /v RealTimeIsUniversal /t REG_DWORD /d 1 /f >> S:\sentinel\log 2>&1 || set failed=setting the clock to UTC failed
if defined failed (echo %failed%> S:\sentinel\result) else (echo ok> S:\sentinel\result)
mountvol S: /d
shutdown /s /t 60 /c "Sentinel: the install is complete"
