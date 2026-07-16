# HFM RAS API — 演示分步指导 (Presenter Guide · 多终端手动版)

> 配套 PPT: `HFM_RASAPI_demo.pptx`
> 演示主机: **<dev-host>**（用户 `<user>`，GSSAPI/Kerberos 登录）
> 辅助脚本: `drive_bmc_pldm.sh`（已放到开发机 `/tmp/hfm-verify/scripts/`）
>
> **本版按你的要求改成分终端手动流程**：分别登录多个 SSH，单独启动 OpenBMC(2600) 和 Zephyr，**能看到两边各自的启动过程**，再分别驱动前向 / 反向 PLDM。
>
> 下文每条命令与期望输出都已在 <dev-host> 上**实测跑通**（前向 `rc=0`、反向 marker 命中）。
>
> **占位符说明**：`<dev-host>` = 你的开发机主机名/IP，`<user>` = 你的登录账号——按你的环境替换即可。

---

## 终端布局一览

| 终端 | 角色 | 主要命令 | 你能看到 |
|------|------|----------|----------|
| **终端 1** | OpenBMC QEMU（socket **server**，先起） | `./launch_openbmc.sh` | AST2600 完整启动日志 → 登录 banner |
| **终端 2** | Zephyr QEMU（socket **client**） | `./launch_hfm.sh` | Zephyr 启动 banner + **反向 PLDM 探测**自动打印 |
| **终端 3** | BMC 侧驱动（前向 PLDM + 反向准备） | `./drive_bmc_pldm.sh` | 前向 `pldmtool` 的 JSON 结果 |

> 三个终端都是 SSH 到同一台 <dev-host>。终端 1/2 是两个 QEMU 的交互式控制台（`-serial mon:stdio`），所以启动过程直接可见——这正是一键脚本看不到的东西。

**为什么需要终端 3？** 这个 OpenBMC 镜像的 `mctp route add` CLI 在内核 6.6.92 上会 busy-loop（见 PPT"已知限制"页），而且 BMC 控制台里没法直接粘贴二进制。所以路由用一个 host 侧的 ARM raw-netlink helper 通过 SSH 流式传入并安装——这就是终端 3 干的活。它同时也负责启动 EID 8 的 base responder，让终端 2 的反向探测能被应答。

---

## 0. 上台前 3 分钟（预热，私下做）

```shell
ssh <user>@<dev-host>
# 确认脚本与依赖都在
ls /tmp/hfm-verify/scripts/{launch_openbmc.sh,launch_hfm.sh,drive_bmc_pldm.sh}
ls /tmp/mctp_route_add /tmp/pldm_base_responder     # 两个 ARM helper（host 侧）
which sshpass
# 杀掉可能残留的 QEMU，清干净 socket
pkill -9 -f qemu-system; rm -f /tmp/hfm-mctp.sock
```

> `launch_openbmc.sh` / `launch_hfm.sh` 会**自己**从 `.gz` 解出 pristine 镜像、并在退出时清理，所以手动流程里**不需要**再单独 `zcat` 恢复镜像。

---

## 1. 终端 1 — 启动 OpenBMC（AST2600），看它启动（PPT 第 7 页）

新开 SSH #1：

```shell
ssh <user>@<dev-host>
cd /tmp/hfm-verify/scripts
./launch_openbmc.sh
```

脚本会打印绿字提示并停在 `Press Enter to continue` —— **按回车**，AST2600 开始启动。

**你会看到**（讲解词）：
> "这是 OpenBMC 在 QEMU 里跑 AST2600。能看到 U-Boot、内核启动、systemd 把服务拉起来。它把第二个串口(UART1/ttyS0)导出成一个 unix socket 当 MCTP 链路的 server；第一个串口就是我们看到的这个控制台。等它出现 `... login:` 就绪了。"

等到出现登录提示：
```
evb-ast2600 login:
```
（登录是 `root / 0penBmc`，前面是零、小写 o——但演示里不用手动登录，终端 3 会通过 SSH 进去。）

> ⚠️ **先起终端 1**（socket server），再起终端 2（client）。顺序反了 Zephyr 会连不上 socket。

---

## 2. 终端 2 — 启动 Zephyr，看它启动（PPT 第 7 页）

再新开 SSH #2：

```shell
ssh <user>@<dev-host>
cd /tmp/hfm-verify/scripts
./launch_hfm.sh
```

同样**按回车**，Zephyr 启动。

**你会看到**（讲解词）：
> "这是 Zephyr 跑在 RISC-V sifive_u 上的 serial_bridge sample。它是 EID 18 的端点，注册了 PLDM Type 0/2 responder 和 MCTP control responder。启动 banner 之后，它的 requester 线程会**主动、反复**去探测 BMC(EID 8)——一开始会一直超时重试(`rc=-116`)，因为 BMC 侧的 MCTP 链路还没拉起来。等下终端 3 把链路和 responder 准备好，这里就会自动打出反向成功。"

启动 banner 大致：
```
*** Booting Zephyr OS build v4.3.0 ***
<inf> serial_bridge: PLDM serial bridge: EID 18 ...
<inf> serial_bridge: MCTP control + Type 0/2 responders ready
<wrn> serial_bridge: BMC GetTID try 1 rc=-116, retrying    <-- 反复重试，正常
```

**这个终端保持不动**，待会儿盯着它看反向成功。

---

## 3. 终端 3 — 前向 PLDM（BMC → Zephyr）+ 反向准备（PPT 第 8–9 页）

第三个 SSH #3。等**终端 1 的 BMC 已经出现 login banner** 后再跑：

```shell
ssh <user>@<dev-host>
cd /tmp/hfm-verify/scripts
./drive_bmc_pldm.sh
```

脚本会：轮询 BMC SSH 就绪 → 流式传入两个 helper → 拉起 `mctpserial0` link/addr → 用 helper 装 EID 18 路由 → 跑前向 `pldmtool` → 停掉 pldmd 并启动 EID 8 的 base responder。

**期望输出（实测）：**
```
[drive] BMC SSH is up
XFER_OK                       # route helper 传好
XFER_OK                       # responder 传好
link-up rc=0
addr rc=0
ifindex=7
route add OK: eid=18 oif=7 mtu=0
helper-route rc=0
========== FORWARD: BMC (EID 8) -> Zephyr (EID 18) ==========
{ "Response": 1 }                                    GetTID rc=0
{ "CompletionCode": "SUCCESS",
  "PLDMTypes": [ base(0), platform(2) ] }            GetPLDMTypes rc=0
{ "CompletionCode": "SUCCESS", "Response": "1.1.0" } GetPLDMVersion rc=0
{ "PDRType": "Terminus Locator PDR", "TID": 1, "EID": 18, ... }  GetPDR rc=0
{ ... "presentReading": 31, "Sensor Enabled", "Sensor Normal" }  GetSensorReading rc=0
========== REVERSE prep: stop pldmd, start base responder on EID 8 ==========
stopped pldmd
responder alive? yes
### DONE. Watch the Zephyr console (terminal 2) for the reverse probe.
```

**讲解词（前向）：**
> "这是 OpenBMC 用它**真实的内核 AF_MCTP 栈 + pldmtool**去发现并轮询 EID 18 的 Zephyr：
> - `GetTID` → TID=1
> - `GetPLDMTypes` → base(0) + platform(2)
> - `GetPDR` → 真实的 Terminus Locator PDR，EID 18
> - `GetSensorReading -i 1 --rearm 0` → **presentReading 31**，即模拟 die 温度 31°C。
> 这就是 HFM 遥测真正在线上流动。"

---

## 4. 回到终端 2 — 看反向 PLDM 自动成功（PPT 第 10 页）

终端 3 启动 responder 后几秒到几十秒内，**终端 2 的 Zephyr 控制台**会自动打出：

```
<inf> serial_bridge: BMC GetTID -> 0x08 (try 20)
<inf> serial_bridge: BMC GetPLDMTypes -> byte0=0x01
<inf> serial_bridge: BMC GetPLDMVersion(BASE) -> 0.0.1
<inf> serial_bridge: Reverse-direction PLDM probe to BMC complete
```

**讲解词（反向 + patch 0009）：**
> "现在反过来——Zephyr 作为 requester 去问 BMC——成功了。这一步最初一直超时，根因很微妙：Zephyr 发请求时 **MCTP tag-owner (TO) 位是 0**。Linux 内核 AF_MCTP 只在 TO=1 时把入站帧投递给绑定 socket，TO=0 被当孤立响应丢掉。修复就是 **patch 0009**：`pldm.c` 里把 `MCTP_MESSAGE_TO_DST` 改成 `MCTP_MESSAGE_TO_SRC`。BMC responder 日志能看到 `RX from EID 18 tag 0x08`——`tag 0x08` 就是 TO=1 的证据。这个修复已合进 RASAPI 分支(PR #1)。"

> 从终端 3 停留的 SSH 里也能看 BMC responder 日志：`cat /tmp/responder.log`（会看到 `RX from EID 18 tag 0x08 ... TX response`）。

---

## 5. 收尾 & 退出

**退出两个 QEMU**：在终端 1 和终端 2 里分别按 **`Ctrl-a`** 松开再按 **`x`**。脚本会自动清理临时镜像和 socket。

**已知限制**（诚实说明，对应 PPT 第 11 页）：
> "mctpd 全自动发现在这个镜像上跑不通——但不是我们代码的问题。内核 6.6.92 有 AF_MCTP netlink-dump busy-loop：mctpd 启动调 `fill_linkmap()` 的 dump 空转，事件循环推不动，所以自动 `SetupEndpoint` 完不成，`mctp route` CLI dump 路径也挂。我们用 raw-netlink helper（只 set 不 dump）绕过，所有 PLDM 命令都在真实内核传输上正确应答。换个 AF_MCTP dump 正常的内核，mctpd 就能自发现。"

---

## 附录 A — 顺序速记（三终端）

```
终端1(SSH#1): cd /tmp/hfm-verify/scripts && ./launch_openbmc.sh   # 回车，等 login banner
终端2(SSH#2): cd /tmp/hfm-verify/scripts && ./launch_hfm.sh       # 回车，看启动+反复重试
终端3(SSH#3): cd /tmp/hfm-verify/scripts && ./drive_bmc_pldm.sh   # 前向 pldmtool + 反向准备
   ↓
终端2 自动打出 "Reverse-direction PLDM probe to BMC complete"
```

---

## 附录 B — 纯手动交互版（进阶，让观众看到你在 BMC 上敲 pldmtool）

如果想更"现场感"，让终端 3 只做**管线准备**，前向命令你亲手在 BMC 上敲：

1. 终端 3 里只跑管线部分（拉链路 + 装路由 + 起 responder），或直接用 `drive_bmc_pldm.sh`（它已经把这些做完了）。
2. 另开一个 SSH 直接进 BMC，手敲前向命令：
   ```shell
   sshpass -p 0penBmc ssh -p 3222 -o StrictHostKeyChecking=no \
     -o HostKeyAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1 \
     root@127.0.0.1
   # 进入 BMC 后：
   pldmtool base GetTID -m 18
   pldmtool base GetPLDMTypes -m 18
   pldmtool platform GetPDR -m 18 -d 0
   pldmtool platform GetSensorReading -m 18 -i 1 --rearm 0    # 注意 --rearm 0 必带
   ```

> 前提：终端 3 已经把 `mctpserial0` 拉起来并装好 EID 18 路由，否则手敲 pldmtool 会超时。

---

## 附录 C — 故障排除

| 症状 | 原因 | 处理 |
|------|------|------|
| 终端 2 Zephyr 报 socket 连不上 | 终端 1 还没起 / socket 没生成 | 确认先跑终端 1，`ls -l /tmp/hfm-mctp.sock` |
| 终端 3 `BMC SSH` 一直等 | dropbear 还没起 | 脚本内置 60×3s 轮询；耐心等或确认终端 1 已出 login banner |
| `helper-route rc` 非 0 | 路由没装上 | 看 `ifindex=` 是否有值；确认 `/tmp/mctp_route_add` 在 host 上 |
| `GetSensorReading rc=106` | 少了 `--rearm 0` | 用带 `--rearm 0` 的命令（脚本已修正） |
| 终端 2 一直 `rc=-116` 不成功 | responder 没起 / 路由没装 | 确认终端 3 打出 `responder alive? yes` 和 `helper-route rc=0` |
| 登录被拒 `gssapi` | Kerberos 票据过期 | 重连一次 |

**彻底重置：**
```shell
pkill -9 -f qemu-system; rm -f /tmp/hfm-mctp.sock
```

---

## 附录 D — 关键 EID / 端口 / 路径速记

| 项 | 值 |
|----|----|
| OpenBMC EID / Zephyr EID | 8 / 18 |
| MCTP socket | `/tmp/hfm-mctp.sock` |
| BMC SSH（QEMU hostfwd） | 端口 3222，`root / 0penBmc` |
| OpenBMC WebUI | `https://127.0.0.1:1443` |
| 前向驱动脚本 | `/tmp/hfm-verify/scripts/drive_bmc_pldm.sh` |
| BMC responder 日志 | BMC 内 `/tmp/responder.log` |
| 退出 QEMU | `Ctrl-a` 然后 `x` |
