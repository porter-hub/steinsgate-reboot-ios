# 命运石之门reboot：iPhone 版

[English](README.md) · **中文**

![iOS 26+](https://img.shields.io/badge/iOS-26%2B-0e1834)
![iPhone 12 或更新](https://img.shields.io/badge/iPhone-A14%20%E6%88%96%E6%9B%B4%E6%96%B0-0e1834)
![Windows · macOS · Linux](https://img.shields.io/badge/%E5%88%B6%E4%BD%9C-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-d6b366)
![不含游戏内容](https://img.shields.io/badge/%E6%B8%B8%E6%88%8F%E5%86%85%E5%AE%B9-%E4%B8%8D%E5%90%AB%EF%BC%88%E7%94%A8%E4%BD%A0%E8%87%AA%E5%B7%B1%E7%9A%84%EF%BC%89-d6b366)

Nintendo Switch 版 **STEINS;GATE 重制版（中文版 v1.0.3）** 的 iPhone 原生移植。这不是模拟器：游戏自己的程序在离线时被改写，直接跑在 iPhone 的 CPU 上，画面由 Metal 绘制。装到手机上的名字是 **命运石之门reboot**。

这个仓库是**安装工具**，不含任何游戏内容。你提供自己的游戏文件和 `prod.keys`，在任意一台 Windows、Mac 或 Linux 电脑上做成 iPhone App，再用 Sideloadly 装到手机上。

---

## 目录

- [需要准备](#需要准备)
- [快速开始](#快速开始)
- [装到 iPhone](#装到-iphone)
- [怎么玩](#怎么玩)
- [原理（以及 Windows 为什么能做 iPhone App）](#原理)
- [常见问题](#常见问题)
- [测试情况](#测试情况)
- [仓库里有什么](#仓库里有什么)
- [法律说明](#法律说明)

## 需要准备

| | |
|---|---|
| **游戏文件** | 从你自己的 Switch 导出的**本体 NSP** 和 **1.0.3 更新 NSP**。必须是移植所用的同一份文件：工具会用 SHA-256 校验，不一致会直接告诉你。只支持 NSP，不支持 XCI。 |
| **密钥** | 同一台 Switch 导出的 `prod.keys`。 |
| **电脑** | Windows 10/11（x64）、Apple 芯片的 Mac，或 Linux（x64 / arm64）。约 **10 GB** 空闲磁盘，第一次运行需要联网。不需要 Xcode，也不需要任何开发工具。 |
| **iPhone** | iOS 26 以上，A14 或更新的芯片（iPhone 12 及之后）。约 **7 GB** 空闲空间。 |
| **Apple ID** | 免费的就行，只用来给 App 签名。 |
| **Sideloadly** | 从 [sideloadly.io](https://sideloadly.io) 下载（Windows / Mac）。Windows 上还要装苹果官网的 iTunes 和 iCloud（不是 Microsoft Store 版），Sideloadly 网站上有链接。 |

## 快速开始

1. **获取安装工具**：`git clone https://github.com/porter-hub/steinsgate-reboot-ios.git`，或者在 GitHub 上下载 ZIP 再解压。
2. **放文件**：把两个 NSP 和 `prod.keys` 放进 **`game-files`** 文件夹。文件名随意，工具按内容识别。
3. **运行 Start**：

   | 系统 | 怎么做 |
   |---|---|
   | Windows | 双击 **`Start-Windows.bat`**。如果出现「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」。 |
   | macOS | 双击 **`Start-macOS.command`**。如果 macOS 不让打开，到「系统设置 → 隐私与安全性」页面底部点「仍要打开」，再双击一次。 |
   | Linux | 在终端运行：`bash start-linux.sh` |

   它会先检查电脑环境。缺 **uv**（用来准备 Python）或 **.NET 10 运行时**时，会问你要不要用官方安装脚本装上：装在你的用户目录，不需要管理员权限。你也可以回答不要，按它给的链接自己装。

   然后它会自动完成八个步骤，大约 10 分钟。最后一行显示 **`PACK OK`** 就是成功了。
4. **取结果**：在 **`output`** 文件夹里：
   - `SteinsGate.ipa`：iPhone 安装包（约 40 MB）；
   - `game-data/`：7 个 `.tar` 文件（共约 5.8 GB）。

中途出错时，工具会用红字写明出了什么问题、为什么。解决后再运行一次 Start，已经做好的步骤会自动跳过。

## 装到 iPhone

1. **开发者模式**：iPhone 上打开「设置 → 隐私与安全性 → 开发者模式」，打开后手机会重启。如果还看不到这个开关，先做第 2 步，装过一次以后就会出现。
2. **安装 App**：用数据线连上 iPhone，在手机上点「信任此电脑」。打开 Sideloadly，把 `SteinsGate.ipa` 拖进去，填你的 Apple ID，点 **Start**。
3. **信任开发者**：iPhone 上打开「设置 → 通用 → VPN 与设备管理」，点你的 Apple ID，选**信任**。
4. **拷入游戏数据**：在电脑的文件共享里，这个 App 叫 **命运石之门reboot**。把 `output/game-data` 里的所有 `.tar` 文件拷进去：
   - **Windows**：在 iTunes 里点左上角的 iPhone 图标，选「文件共享 → 命运石之门reboot」，把文件拖进右边的文档列表。用「Apple 设备」App 的话：选中 iPhone →「文件」→「命运石之门reboot」。
   - **macOS**：在访达侧边栏选中 iPhone →「文件」，把文件拖到「命运石之门reboot」上。
   - **Linux**：没有官方的文件共享。可以用 libimobiledevice 的 `ifuse --documents`（未经测试），或者换一台 Windows / Mac 来拷。

   等进度走完。
5. **开始游戏**：第一次启动会先后显示「正在导入游戏数据」和「正在准备着色器」两个进度画面（共约一分钟，只有这一次），然后进入游戏。导入完成后，`.tar` 文件会自动删掉。

> **每 7 天一次**：免费 Apple ID 的签名只有 7 天有效。App 打不开时，用 Sideloadly 把 `SteinsGate.ipa` 再装一次即可，存档和游戏数据都会保留。**不要先删除 App**：删 App 会连存档一起删掉。

## 怎么玩

任何画面都能直接用触屏操作，不需要虚拟手柄：

| 手势 | 作用 |
|---|---|
| 单击 | A：推进文字、确认 |
| 双击，或双指轻点 | B：返回、取消 |
| 单指拖动 | 摇杆，手指不离开就一直按着：移动光标、滚动菜单、拨电话 |
| 双指上滑 / 下滑 | 履历 / 系统菜单 |
| 长按 | 已读快进开 / 关（游戏的 R 键） |
| 三指轻点 | 显示 / 隐藏虚拟手柄 |

屏幕边上常驻两个小按钮，放在远离灵动岛的一侧：

- 🎮 在三种状态间切换虚拟手柄：隐藏 → 只显示上方按键（快进、强制快进、手机、+、−）→ 全部；
- ⚙ 打开设置：透明度、闲置淡出、每种手势一个开关、帧率显示，以及存档的导出和导入。

也可以用游戏手柄。连上手柄时，屏幕上的按键会自动隐藏。

## 原理

> **「Windows 上没有 Xcode，怎么做出 iPhone App？」**
>
> 根本不需要编译。App 本身（代替 Switch 系统的那部分代码、Metal 图形、触屏操作）已经在 Mac 上编译好，就放在这个仓库的 `kit/shell/Port-shell.zip` 里。它是一个**空壳**：不含游戏，所以可以分享。电脑用你的文件做的全是数据处理，普通的 Python 和 .NET 在任何系统上都能做。唯一需要苹果的一步是签名，由 Sideloadly 完成。

```
你的本体 NSP + 更新 NSP + prod.keys
        │  1. 校验：两个文件的 SHA-256 必须对得上
        │  2. 解包（密钥只放进私有临时文件夹，用完立刻删除，从不显示）
        ▼
游戏程序（ExeFS）                          游戏数据（RomFS，5.8 GB）
        │  3. 离线改写 AArch64 代码，让它直接在     │
        │     iOS 上跑（运行时不修改任何代码）       │
        │  4. → Guest.framework                     │
        │  5. 487 个着色器翻译成 Metal 源码         │
        │  +  游戏自带的图标                        │  7. → output/game-data/*.tar
        ▼                                           ▼
  kit/shell/Port-shell.zip（空壳）          你拷进 App，它在第一次启动时导入
        │  6. 全部装进去 → output/SteinsGate.ipa
        ▼
Sideloadly 用你的 Apple ID 签名 → iPhone
```

每个产物都会逐个文件和安装工具里记录的 SHA-256 比对。这些值来自开发者用自己的文件跑的一次完整流程，那次做出的安装包随后在 iPhone 上实测过。所以不管在哪个系统上，你做出来的安装包都和实测过的那份逐字节相同。只要有一处不同，工具就会停下，而不是交给你一个可能不能用的安装包。

着色器以 Metal 源码的形式交付，因为只有苹果的工具能提前编译 Metal。iPhone 在第一次启动时编译一次（「正在准备着色器」），之后一直用编译好的结果。

## 常见问题

| 提示或问题 | 怎么办 |
|---|---|
| 「game-files 文件夹里缺东西」 | 三个文件缺了一个，或者 NSP 不是预期的那份。只支持中文版本体加 1.0.3 更新，而且必须是 NSP。 |
| 「解包失败」 | 多半是 `prod.keys` 太旧，或者 NSP 不完整。请从系统较新的 Switch 重新导出密钥。 |
| 「和安装工具预期的结果不一致」 | 你的文件或工具和实测时的不一样。再运行一次 Start；还是这样的话，把这一屏截图反馈。 |
| 「磁盘空间不够」 | 在这个文件夹所在的盘上腾出约 10 GB。 |
| Sideloadly 提示 *maximum number of apps* | 免费 Apple ID 在一台设备上最多装 3 个自签名 App，删掉一个。 |
| App 显示「还没有游戏数据」 | 把 `.tar` 文件拷进去（「装到 iPhone」第 4 步），从多任务界面关掉 App 再打开。 |
| App 显示「导入游戏数据失败」 | 可能是还在拷、iPhone 空间不够（约需 7 GB），或者文件不完整。处理好以后关掉 App 再打开，会从中断处继续导入。 |
| App 显示「安装包不完整」 | IPA 缺了文件。用 Start 重新做一次，再重新安装。 |
| 用了一周 App 打不开了 | 用 Sideloadly 重新安装（见上面「每 7 天一次」）。 |

## 测试情况

| | |
|---|---|
| ✅ macOS（Apple 芯片）上制作 | 从全新克隆开始，和记录的安装包逐字节一致 |
| ✅ iPhone 17 Pro，iOS 26，免费 Apple ID | 安装、导入数据、准备着色器（487 个，29 秒，0 失败），游戏 60 帧运行，有声音 |
| ✅ 安装工具不含游戏内容 | 发布前每个文件都和游戏文件做过字节比对 |
| ⚠️ Windows | 还没实际运行过。已按 Windows 的情况写好并检查过：换行符、路径、控制台编码。 |
| ⚠️ Linux | 只能制作；安装要靠第三方工具（Sideloadly 没有 Linux 版），未经测试 |
| ⚠️ 其他 iPhone | 未测试；内存比 17 Pro 小的机型可能不太稳定 |
| ⚠️ 已知问题 | 播动画时离开 App 再回来，动画画面可能停住（正在修） |

## 仓库里有什么

```
README.md, README.zh.md      本说明（English / 中文）
Start-Windows.bat            Windows 入口 → kit/bootstrap.ps1
Start-macOS.command          macOS 入口   → kit/bootstrap.sh
start-linux.sh               Linux 入口   → kit/bootstrap.sh
game-files/                  放你的 NSP 和 prod.keys（git 会忽略）
kit/
  pack.py                    打包程序（上面那八步）
  bootstrap.sh / .ps1        检查 uv 和 .NET，提供官方安装脚本
  shell/Port-shell.zip       不含游戏内容的 App 空壳（故意做成 zip：它本身不能直接安装）
  src/                       本移植自己的工具：改写器、着色器链、密钥过滤、游戏事实（game.json）
  dotnet/                    解包工具（LibHac）和着色器翻译器（Ryujinx），用 dotnet 运行
  spirv-cross/               各系统的 SPIRV-Cross，从同一份固定版本的源码编译
  expect.json                实测版本所有产物的 SHA-256
  dmsh_kit.lock              上面各个工具的指纹
  LICENSES/                  第三方许可证
work/, output/               运行 Start 时生成（git 会忽略）
```

## 法律说明

这个仓库**不含任何游戏内容**：没有游戏程序、美术、文字、声音、视频、着色器或密钥。所有和游戏有关的文件，都是在你的电脑上、用你合法持有的拷贝现做的，只留在你的电脑和 iPhone 上。**请不要分发做好的 `SteinsGate.ipa` 或游戏数据**：它们包含游戏内容。STEINS;GATE 是其权利人的商标；这是非官方的爱好者项目，与权利人无关，也未获其认可。

第三方组件及其许可证：[`kit/LICENSES`](kit/LICENSES/README.txt)。
