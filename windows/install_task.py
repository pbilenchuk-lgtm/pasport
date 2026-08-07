"""Регистрация монитора в планировщике задач Windows.

Задача создаётся из XML, а не командой schtasks с ключами: только XML позволяет
задать то, без чего на домашнем ноутбуке монитор работать не будет —
перезапуск при сбое, работу от батареи и повторный старт после сна.

Запускается из setup.bat, вручную вызывать не нужно.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

TASK_NAME = "PassportQueueMonitor"

# Настройки задачи. Ключевые места прокомментированы: их правка меняет
# поведение на ноутбуке, который засыпает.
TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Монитор свободных дат электронной очереди паспортного сервиса</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
      <Repetition>
        <Interval>PT15M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>{wake}</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def build_xml(python: str, script: str, workdir: str, user: str, wake: bool) -> str:
    return TASK_XML.format(
        python=python,
        script=script,
        workdir=workdir,
        user=user,
        wake="true" if wake else "false",
    )


def main() -> int:
    if os.name != "nt":
        print("Этот установщик рассчитан на Windows.")
        return 1

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    script = os.path.join(root, "monitor.py")

    if not os.path.exists(python):
        print(f"Не найден {python}. Сначала запусти setup.bat.")
        return 1

    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    wake = "--wake" in sys.argv

    xml = build_xml(python, script, root, user, wake)

    # schtasks читает файл задачи в UTF-16 — в другой кодировке он его не примет.
    handle, path = tempfile.mkstemp(suffix=".xml")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-16") as fh:
            fh.write(xml)

        result = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Не удалось создать задачу:")
            print(result.stdout or "", result.stderr or "")
            return 1

        print(f"Задача «{TASK_NAME}» создана.")
        if wake:
            print("Компьютер будет просыпаться для проверок.")

        # Сразу запускаем, чтобы не ждать следующего входа в систему.
        subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True)
        print("Монитор запущен в фоне.")
        return 0
    finally:
        os.unlink(path)


if __name__ == "__main__":
    sys.exit(main())
