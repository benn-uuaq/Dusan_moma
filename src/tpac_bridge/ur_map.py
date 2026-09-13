#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ur_map.py — Universal Robots Modbus 서버 레지스터 맵
=====================================================
UR 컨트롤러(CB3 / e-Series)가 502 포트로 제공하는 Modbus 서버의 주소 배치.
외부 장비(TPAC 등)가 UR 기준으로 만들어진 경우, 이 배치대로 값을 채워두면
로봇 없이도 붙는지 시험할 수 있다.

TCP 좌표는 400~405 다. (S-series 는 384~389 이라 서로 다르다)
"""

# ---------------------------------------------------------------- I/O
REG_INPUTS          = 0      # Inputs bits 0-15
REG_OUTPUTS         = 1      # Outputs bits 0-15
REG_SET_OUT_MASK    = 2
REG_CLR_OUT_MASK    = 3
REG_AIN0            = 4
REG_AIN0_DOMAIN     = 5
REG_AIN1            = 6
REG_AIN1_DOMAIN     = 7
REG_AIN2_TOOL       = 8
REG_AIN2_DOMAIN     = 9
REG_AIN3_TOOL       = 10
REG_AIN3_RANGE      = 11
REG_AOUT0           = 16
REG_AOUT0_DOMAIN    = 17
REG_AOUT1           = 18
REG_AOUT1_DOMAIN    = 19
REG_TOOL_VOLTAGE    = 20     # 0V / 12V / 24V
REG_TOOL_DIN        = 21
REG_TOOL_DOUT       = 22
REG_EUROMAP_IN0     = 24
REG_EUROMAP_IN1     = 25
REG_EUROMAP_OUT0    = 26
REG_EUROMAP_OUT1    = 27
REG_EUROMAP_24V     = 28
REG_EUROMAP_24I     = 29
REG_CFG_INPUTS      = 30
REG_CFG_OUTPUTS     = 31
REG_CFG_OUT_MASK    = 32
REG_CFG_OUT_CLEAR   = 33

REG_GP_START        = 128    # 128~255 General purpose 16bit registers

# ---------------------------------------------------------------- Robot state
REG_VER_HIGH        = 256    # Controller version high number
REG_VER_LOW         = 257    # Controller version low number
REG_ROBOT_MODE      = 258    # CB3/3.1: Disconnected=0 ... Running=7
REG_POWER_ON        = 260    # isPowerOnRobot
REG_SECURITY_STOP   = 261    # isSecurityStopped
REG_EMERGENCY_STOP  = 262    # isEmergencyStopped
REG_TEACH_BUTTON    = 263    # isTeachButtonPressed
REG_POWER_BUTTON    = 264    # isPowerButtonPressed
REG_SAFETY_SIGNAL   = 265    # isSafetySignalSuchThatWeShouldStop

REG_JOINT_ANGLE     = 270    # 270~275  [mrad]
REG_JOINT_VELOCITY  = 280    # 280~285  [mrad/s]
REG_JOINT_CURRENT   = 290    # 290~295  [mA]
REG_JOINT_TEMP      = 300    # 300~305  [℃]
REG_JOINT_MODE      = 310    # 310~315
REG_JOINT_REVOLUTION = 320   # 320~325  (3.1 이상)

# ---------------------------------------------------------------- TCP
REG_TCP_POSE        = 400    # 400~402 X,Y,Z [0.1mm] / 403~405 Rx,Ry,Rz [mrad]
REG_TCP_SPEED       = 410    # 410~412 [mm/s]        / 413~415 [mrad/s]
REG_TCP_OFFSET      = 420    # 420~422 [mm] (tool frame) / 423~425 [mrad]

REG_ROBOT_CURRENT   = 450    # [mA]
REG_IO_CURRENT      = 451    # [mA]

# ---------------------------------------------------------------- Tool
REG_TOOL_STATE      = 768
REG_TOOL_TEMP       = 769
REG_TOOL_CURRENT    = 770

REG_GUI_STATE       = 1024

# ---------------------------------------------------------------- RT Machine
REG_RT_SPLIT_TIME   = 2048   # write: 시각을 2049~2053 에 래치
REG_RT_MS           = 2049
REG_RT_SEC          = 2050
REG_RT_MIN          = 2051
REG_RT_HOUR         = 2052
REG_RT_DAY          = 2053

MAX_ADDR            = 2053

# ---------------------------------------------------------------- Coils
COIL_INPUTS         = 0      # 0~15
COIL_OUTPUTS        = 16     # 16~31
COIL_SET_OUT_MASK   = 32     # 32~47
COIL_CLR_OUT_MASK   = 48     # 48~63
COIL_EUROMAP_IN0    = 64     # 64~79
COIL_EUROMAP_IN1    = 80     # 80~95
COIL_EUROMAP_OUT0   = 96     # 96~111
COIL_EUROMAP_OUT1   = 112    # 112~127
COIL_CFG_INPUTS     = 128    # 128~135
COIL_CFG_OUTPUTS    = 136    # 136~143
COIL_CFG_OUT_MASK   = 144    # 144~151
COIL_CFG_OUT_CLEAR  = 152    # 152~159
COIL_POWER_ON       = 260
COIL_PROTECTIVE     = 261
COIL_EMERGENCY      = 262
COIL_TEACH_BUTTON   = 263
COIL_POWER_BUTTON   = 264
COIL_SAFETY_SIGNAL  = 265
MAX_COIL            = 265

ROBOT_MODE_TXT = {
    0: "Disconnected", 1: "Confirm_safety", 2: "Booting", 3: "Power_off",
    4: "Power_on", 5: "Idle", 6: "Backdrive", 7: "Running",
}
JOINT_MODE_TXT = {
    236: "SHUTTING_DOWN", 237: "PART_D_CALIBRATION", 238: "BACKDRIVE",
    239: "POWER_OFF", 245: "NOT_RESPONDING", 246: "MOTOR_INIT",
    247: "BOOTING", 248: "PART_D_CALIB_ERROR", 249: "BOOTLOADER",
    250: "CALIBRATION", 252: "FAULT", 253: "RUNNING", 255: "IDLE",
}

JOINT_NAMES = ["Base", "Shoulder", "Elbow", "Wrist1", "Wrist2", "Wrist3"]
TCP_NAMES = ["X", "Y", "Z", "Rx", "Ry", "Rz"]


# ---------------------------------------------------------------- 주소 이름
def addr_name(a):
    """레지스터 주소를 사람이 읽는 이름으로. 외부 장비가 뭘 읽는지 볼 때 쓴다."""
    table = [
        (0, 0, "Inputs bits 0-15"),
        (1, 1, "Outputs bits 0-15"),
        (2, 3, "Set/Clear outputs mask"),
        (4, 11, "Analog input"),
        (16, 19, "Analog output"),
        (20, 22, "Tool voltage / digital IO"),
        (24, 29, "Euromap67"),
        (30, 33, "Configurable IO"),
        (34, 127, "Reserved"),
        (128, 255, "General purpose register"),
        (256, 256, "Controller version high"),
        (257, 257, "Controller version low"),
        (258, 258, "Robot mode"),
        (260, 260, "isPowerOnRobot"),
        (261, 261, "isSecurityStopped"),
        (262, 262, "isEmergencyStopped"),
        (263, 263, "isTeachButtonPressed"),
        (264, 264, "isPowerButtonPressed"),
        (265, 265, "isSafetySignal"),
        (270, 275, "Joint angle [mrad]"),
        (280, 285, "Joint velocity [mrad/s]"),
        (290, 295, "Joint current [mA]"),
        (300, 305, "Joint temperature [C]"),
        (310, 315, "Joint mode"),
        (320, 325, "Joint revolution count"),
        (400, 405, "TCP pose  X,Y,Z[0.1mm] Rx,Ry,Rz[mrad]"),
        (410, 415, "TCP speed [mm/s, mrad/s]"),
        (420, 425, "TCP offset (tool frame)"),
        (450, 450, "Robot current [mA]"),
        (451, 451, "I/O current [mA]"),
        (768, 770, "Tool state / temp / current"),
        (1024, 1024, "GUI state (reserved)"),
        (2048, 2053, "RT machine time"),
    ]
    for lo, hi, name in table:
        if lo <= a <= hi:
            if lo != hi and hi - lo == 5:
                idx = a - lo
                sub = (JOINT_NAMES[idx] if "Joint" in name
                       else TCP_NAMES[idx] if "TCP" in name else str(idx))
                return f"{name} [{sub}]"
            return name
    return "정의되지 않음"
