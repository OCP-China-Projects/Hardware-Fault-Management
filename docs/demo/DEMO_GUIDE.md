# HFM RAS API — 演示分步指导 (Presenter Guide · 多终端手动版)

> 配套 PPT: `HFM_RASAPI_demo.pptx`
> 演示主机: **<dev-host>**（用户 `<user>`，GSSAPI/Kerberos 登录）
> 辅助脚本: `drive_bmc_pldm.sh`（已放到开发机 `/tmp/hfm-verify/scripts/`）
>
> **本版按你的要求改成分终端手动流程**：分别登录多个 SSH，单独启动 OpenBMC(2600) 和 Zephyr，**能看到两边各自的启动过程**，再驱动前向 PLDM，反向 PLDM 会自动完成。
>
> **全自动发现**：镜像已合入三处修复（内核 patch 0010 + Zephyr control-responder patch 0011 + BMC systemd 顺序 patch 0006），开机后 mctpd **零手动**自动发现 EID 18、自动装内核路由。下文每条命令与期望输出都已在 <dev-host> 上**实测跑通**。
>
> **占位符说明**：`<dev-host>` = 你的开发机主机名/IP，`<user>` = 你的登录账号——按你的环境替换即可。

---

## 终端布局一览

| 终端 | 角色 | 主要命令 | 你能看到 |
|------|------|----------|----------|
| **终端 1** | OpenBMC QEMU（socket **server**，先起） | `./launch_openbmc.sh` | AST2600 完整启动日志 → 登录 banner |
| **终端 2** | Zephyr QEMU（socket **client**） | `./launch_hfm.sh` | Zephyr 启动 banner + **反向 PLDM 探测自动成功** |
| **终端 3** | BMC 侧驱动（前向 PLDM 查询） | `./drive_bmc_pldm.sh` | 自动发现的路由 + 前向 `pldmtool` 的 JSON 结果 |

> 三个终端都是 SSH 到同一台 <dev-host>。终端 1/2 是两个 QEMU 的交互式控制台（`-serial mon:stdio`），所以启动过程直接可见——这正是一键脚本看不到的东西。

**为什么需要终端 3？** 它进 BMC 展示 mctpd **已经自动装好**的路由（`mctp route` 里出现 EID 18），再用 `pldmtool` 跑前向查询。不再需要手动装路由或 raw-netlink helper——那是旧内核 bug 时期的绕行方案，现在三处修复合入后已经不需要了。

---

## 0. 上台前 3 分钟（预热，私下做）

```shell
ssh <user>@<dev-host>
# 确认脚本与依赖都在
ls /tmp/hfm-verify/scripts/{launch_openbmc.sh,launch_hfm.sh,drive_bmc_pldm.sh}
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
> "这是 OpenBMC 在 QEMU 里跑 AST2600。能看到 U-Boot、内核启动、systemd 把服务拉起来。注意启动日志里 MCTP 的顺序：**先** `Started MCTP configuration for serial ttyS0`（把串口链路拉起来、装本地 EID 8），**再** `Started MCTP control protocol daemon`（mctpd），最后 `mctp-setup-endpoint` 自动发现远端。这个顺序是 patch 0006 的关键——mctpd 只在启动那一刻快照一次链路表，链路必须先于它存在。"

等到出现登录提示：
```
evb-ast2600 login:
```
（登录是 `root / 0penBmc`，前面是零、小写 o——但演示里不用手动登录，终端 3 会通过 SSH 进去。）

> ⚠️ **先起终端 1**（socket server），再起终端 2（client）。顺序反了 Zephyr 会连不上 socket。

---

## 2. 终端 2 — 启动 Zephyr，看它启动 + 反向自动成功（PPT 第 7、10 页）

再新开 SSH #2：

```shell
ssh <user>@<dev-host>
cd /tmp/hfm-verify/scripts
./launch_hfm.sh
```

同样**按回车**，Zephyr 启动。

**你会看到**（讲解词）：
> "这是 Zephyr 跑在 RISC-V sifive_u 上的 serial_bridge sample。它是 EID 18 的端点，注册了 PLDM Type 0/2 responder 和 MCTP control responder。启动 banner 之后，它的 requester 线程会**主动、反复**去探测 BMC(EID 8)——一开始会超时重试(`rc=-116`)，因为 BMC 侧的 mctpd 还没完成自动发现。一旦 mctpd 装好路由，反向探测就会**自动打出成功**，无需任何手动干预。"

启动 banner 大致：
```
*** Booting Zephyr OS build v4.3.0 ***
<inf> serial_bridge: PLDM serial bridge: EID 18 ...
<inf> serial_bridge: MCTP control + Type 0/2 responders ready
<wrn> serial_bridge: BMC GetTID try 1 rc=-116, retrying    <-- 反复重试，正常
...
<inf> serial_bridge: BMC GetTID -> 0x01 (try 17)
<inf> serial_bridge: BMC GetPLDMTypes -> byte0=0x1d
<inf> serial_bridge: BMC GetPLDMVersion(BASE) -> 1.0.0
<inf> serial_bridge: Reverse-direction PLDM probe to BMC complete
```

**讲解词（反向 + patch 0009/0011）：**
> "反向——Zephyr 作为 requester 去问 BMC——自动成功了。这里有两个对称的 tag-owner (TO) 位修复：
> - **patch 0009**（requester 路径）：Zephyr 发请求必须 TO=1，Linux 内核 AF_MCTP 只在 TO=1 时把入站帧投给绑定 socket，否则当孤立响应丢掉。
> - **patch 0011**（responder 路径）：Zephyr 回 control 响应必须 TO=0，否则 mctpd 的物理寻址发现查询匹配不上、`SetupEndpoint` 超时。
> 两个修复加上内核 patch 0010，mctpd 才能开机零手动自动发现。"

**这个终端保持不动**，反向成功后继续盯着终端 3 的前向结果。

---

## 3. 终端 3 — 前向 PLDM（BMC → Zephyr），展示自动发现的路由（PPT 第 8–9 页）

第三个 SSH #3。等**终端 1 的 BMC 已经出现 login banner** 后再跑：

```shell
ssh <user>@<dev-host>
cd /tmp/hfm-verify/scripts
./drive_bmc_pldm.sh
```

脚本会：轮询 BMC SSH 就绪 → 展示 mctpd **自动装好**的路由（`mctp route` 里有 EID 18） → 跑前向 `pldmtool`。**不再**需要传 helper、装路由或改 pldmd。

**期望输出（实测）：**
```
[drive] BMC SSH is up
### mctpd auto-discovered route (no manual route add):
eid min 18 max 18 net 1 dev mctpserial0 mtu 68
eid min 8 max 8 net 1 dev mctpserial0 mtu 0
### D-Bus endpoints published by mctpd:
endpoints/18
endpoints/8
========== FORWARD: BMC (EID 8) -> Zephyr (EID 18) ==========
{ "Response": 1 }                                    GetTID rc=0
{ "CompletionCode": "SUCCESS",
  "PLDMTypes": [ base(0), platform(2) ] }            GetPLDMTypes rc=0
{ "CompletionCode": "SUCCESS", "Response": "1.1.0" } GetPLDMVersion rc=0
{ "PDRType": "Terminus Locator PDR", "TID": 1, "EID": 18, ... }  GetPDR rc=0
{ ... "presentReading": 31, "Sensor Enabled", "Sensor Normal" }  GetSensorReading rc=0
### DONE. Reverse probe already completed on the Zephyr console (terminal 2).
```

**讲解词（前向）：**
> "先看 `mctp route`——EID 18 那条路由是 **mctpd 自己装的**，不是我们手动加的。mctpd 通过 `SetupEndpoint` 物理寻址发现了 Zephyr，采纳它为 EID 18，自动装了内核路由和 neighbour，还在 D-Bus 上发布了 endpoints/18 给 pldmd。
> 然后是 OpenBMC 用**真实的内核 AF_MCTP 栈 + pldmtool**去轮询 EID 18：
> - `GetTID` → TID=1
> - `GetPLDMTypes` → base(0) + platform(2)
> - `GetPDR` → 真实的 Terminus Locator PDR，EID 18
> - `GetSensorReading -i 1 --rearm 0` → **presentReading 31**，即模拟 die 温度 31°C。
> 这就是 HFM 遥测真正在线上流动，而且全自动。"

---

## 4. 收尾 & 退出

**退出两个 QEMU**：在终端 1 和终端 2 里分别按 **`Ctrl-a`** 松开再按 **`x`**。脚本会自动清理临时镜像和 socket。

**如何做到全自动**（对应 PPT 第 11 页 "三处修复"）：
> "早期这个镜像的 mctpd 自动发现跑不通，我们定位到三个独立根因并逐一修复：
> 1. **内核 (patch 0010)**：6.6.92 的 `for_each_netdev_dump()` 用 `xa_for_each_start()`，游标在遍历结束时不推进，导致 AF_MCTP 地址 dump (`mctp_dump_addrinfo`) 永不发 `NLMSG_DONE`、mctpd 100% CPU 空转。backport 上游 `cfa7fa02078d` 修复。
> 2. **Zephyr control responder (patch 0011)**：control 响应误置 TO=1，被 BMC 内核当新请求，mctpd 发现查询超时。改 TO=0。
> 3. **BMC systemd 顺序 (patch 0006)**：mctpd 只在启动时快照一次链路表，故串口链路必须先于 mctpd 建立。拆成 `mctp-local.service`(Before mctpd) + `mctp-setup-endpoint.service`(After mctpd)。
> 三个修复都已合入 RASAPI 分支，现在开机零手动全链路通。"

---

## 附录 A — 顺序速记（三终端）

```
终端1(SSH#1): cd /tmp/hfm-verify/scripts && ./launch_openbmc.sh   # 回车，等 login banner
终端2(SSH#2): cd /tmp/hfm-verify/scripts && ./launch_hfm.sh       # 回车，看启动+反向自动成功
终端3(SSH#3): cd /tmp/hfm-verify/scripts && ./drive_bmc_pldm.sh   # 展示自动路由 + 前向 pldmtool
   ↓
终端2 自动打出 "Reverse-direction PLDM probe to BMC complete"
终端3 打出前向 5 条 pldmtool 全 rc=0
```

---

## 附录 B — 纯手动交互版（进阶，让观众看到你在 BMC 上敲 pldmtool）

如果想更"现场感"，让终端 3 只做**展示**，前向命令你亲手在 BMC 上敲：

1. 另开一个 SSH 直接进 BMC，先看自动发现的路由，再手敲前向命令：
   ```shell
   sshpass -p 0penBmc ssh -p 3222 -o StrictHostKeyChecking=no \
     -o HostKeyAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1 \
     root@127.0.0.1
   # 进入 BMC 后：
   mctp route                                                 # EID 18 已自动装好
   pldmtool base GetTID -m 18
   pldmtool base GetPLDMTypes -m 18
   pldmtool platform GetPDR -m 18 -d 0
   pldmtool platform GetSensorReading -m 18 -i 1 --rearm 0    # 注意 --rearm 0 必带
   ```

> 前提：mctpd 已完成自动发现（`mctp route` 里有 EID 18）。若还没出现，稍等几秒——`mctp-setup-endpoint.service` 有 40×3s 重试循环，会等 Zephyr 端点应答。

---

## 附录 C — 故障排除

| 症状 | 原因 | 处理 |
|------|------|------|
| 终端 2 Zephyr 报 socket 连不上 | 终端 1 还没起 / socket 没生成 | 确认先跑终端 1，`ls -l /tmp/hfm-mctp.sock` |
| 终端 3 `BMC SSH` 一直等 | dropbear 还没起 | 脚本内置 60×3s 轮询；耐心等或确认终端 1 已出 login banner |
| `mctp route` 里没有 EID 18 | 自动发现还没完成 / Zephyr 未就绪 | 等几秒（重试循环）；确认终端 2 已启动且 socket 已连 |
| `GetSensorReading rc=106` | 少了 `--rearm 0` | 用带 `--rearm 0` 的命令（脚本已修正） |
| 终端 2 一直 `rc=-116` 不成功 | mctpd 还没装路由 | 确认终端 1 启动日志里 mctp-local 在 mctpd 之前；`systemctl status mctp-setup-endpoint` |
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
| 退出 QEMU | `Ctrl-a` 然后 `x` |
