@echo off
rem Sentinel's agent, from the share disk sentineld attaches at every start (the volume holding
rem windows\sentineld-agent.exe), so a new agent needs no reinstall; started again when it stops.
rem Registered at boot as SYSTEM by the "Sentinel agent" task (agent-task.xml).
:loop
for %%d in (D E F G H I J K L M N O P Q R S T U V W Y Z) do if exist %%d:\windows\sentineld-agent.exe (
  call :vulkan %%d
  %%d:\windows\sentineld-agent.exe
  goto :wait
)
:wait
ping -n 3 127.0.0.1 >nul
goto :loop

rem The Vulkan driver (Venus, packages/sentineld/guest/vulkan) from the share into Program Files, named
rem as the display adapter's Vulkan driver in place of viogpu3d's own, whose manifest the loader cannot
rem open (and then fails every application). A driver in use is replaced at the next boot. Both builds
rem are named, ARM64 and x64: an x64 application under emulation reads the same value (it is not WOW64)
rem and the loader skips the one it cannot load.
:vulkan
if not exist %1:\windows\vulkan\venus_icd.json exit /b
robocopy %1:\windows\vulkan "%ProgramFiles%\Sentinel\Vulkan" /mir /r:0 /w:0 >nul
powershell -NoProfile -Command "Get-PnpDevice -PresentOnly | Where-Object { $_.HardwareID -like 'PCI\VEN_1AF4&DEV_1050*' } | ForEach-Object { $key = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_Driver).Data; $dir = $env:ProgramFiles + '\Sentinel\Vulkan'; New-ItemProperty -Force -Path ('HKLM:\SYSTEM\CurrentControlSet\Control\Class\' + $key) -Name VulkanDriverName -PropertyType MultiString -Value @(($dir + '\venus_icd.json'), ($dir + '\venus_icd_x64.json')) }" >nul 2>&1
exit /b
