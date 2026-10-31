# SOLIDWORKS 模型体检 (sw-health-check)

扫描一批 SOLIDWORKS 模型，查出**欠定义草图**、**报错特征**、**被压缩特征**；
也能检查 **STEP / IGES 等导入文件**的实体完整性与几何有效性。

> 定位：**通用的模型质量门禁**。不绑定任何建模工具——不管模型是 AI 生成的、外协发来的、网上下载的、还是自己几个月前画的，只要进了 SOLIDWORKS，它都能检查。

## 它真正解决的问题

一批模型交到你手上，你不知道里面埋了什么：

- 草图是蓝色的（欠定义），一动就跑偏
- 特征树里有黄色感叹号，重建就报错
- 别人的模型，不知道有没有坑

这些问题的共同点是：**SOLIDWORKS 不会主动告诉你，只有出事了才发现。**

这个工具做的事，就是让你**在出事之前**知道。

## 核心能力：查

```bash
python sw_health_check.py "D:\你的\模型目录"
```

逐个打开、遍历完整特征树、检查每个草图和特征，输出 HTML 报告。

**实测：60 个文件、260 个草图，约 8 分钟扫完。**

它不只说「有问题」，还说清楚是什么问题：

```
[欠定义] 草图1：12 条图元（直线 9、圆弧 3），其中 5 条没有任何几何关系（圆弧6、圆弧7、圆弧8、直线14、直线18），建议优先检查这几条
[欠定义] 草图2：11 条图元（直线 11），全部已有几何关系，缺的通常是尺寸或定位
[欠定义] 草图4：草图里没有可识别的图元（可能只包含点或参考线）
```

这类信息人工排查极慢——12 条图元里哪 5 条没约束，肉眼一条条看过去要好几分钟。

## 支持的外部格式：STEP / IGES

```bash
python sw_health_check.py "D:\一批外部模型"
```

支持的扩展名：`.step` `.stp` `.igs` `.iges` `.x_t` `.x_b` `.sat`

**注意：这类文件导入 SOLIDWORKS 后是没有特征树、没有草图的「哑实体」**，所以检查维度完全不同：

| 检查项 | 说明 |
|---|---|
| 能否导入 | 文件损坏会明确报告，不会崩 |
| 实体数量 | 导入后有几个实体 |
| **是否封闭** | 只有曲面没有实体 → 无法加工、无法算体积 |
| **几何有效性** | `IBody2.Check2()` 的 36 种故障码：实体损坏、面自相交、几何退化、壳相交…… |
| 实体规模 | 每个实体的面数、边数 |

输出示例：

```
- good.step：1 个实体（10 面 / 24 边）几何检查通过
- broken.step：导入失败（SOLIDWORKS 返回 False，格式可能损坏）
- open.step：2 个曲面 → 模型不封闭，无法直接加工
```

**这个能力是为「AI 生成模型」准备的**：AI 输出的 STEP 经常有「只有曲面没封闭」「面自相交」这类问题，传统方式要人工一个个打开看，现在可以批量筛。

**自动修复（`--fix`）对导入格式无效**——哑实体没有草图可修。

## 实验性能力：自动修

```bash
python sw_health_check.py "D:\你的\模型目录" --fix
```

对欠定义草图调用 SOLIDWORKS 自带的「自动标注尺寸」（`AutoDimension2`）。

### 先说清楚：效果有限

**它的成功率随草图复杂度急剧下降。**

| 场景 | 实测结果 |
|---|---|
| 结构简单、图元少的草图 | 修好率高（第一批 16 个里修好 13 个） |
| 图元多、几何松散的草图 | 要么算不出解，要么**算过头变成过定义** |
| 空草图（只有点/文字） | 完全修不了 |

在剩下的复杂草图里继续跑，结果是 **10 个文件、83 秒、修复 0 个、1 个被修成过定义**（已拦截，未保存）。

**所以：不要指望它替你把所有欠定义修好。** 它的定位是「能自动修的帮你修掉，修不动的如实告诉你」。

### 安全设计

改模型文件是危险操作，所以：

1. **默认不修复**，必须显式加 `--fix`
2. **修复前自动备份**为同目录的 `.bak`（已有备份不覆盖，保留最早的原始版本）
3. **只在「修好了且没变糟」时存盘**——如果某个草图修复后变成「过定义」，整个文件跳过不保存
4. **图元超过 10 条的草图自动跳过**（实测 15 条图元要算 9 分钟，不值得）
5. 备份失败就跳过该文件

### 还原方法

删掉修复后的 `.SLDPRT`，把同名 `.SLDPRT.bak` 改回 `.SLDPRT` 即可。

### 修复不保证什么

- **只修「欠定义」这个状态，不保证标注好看。** 自动加的尺寸可能堆在一起。
- **不是 100% 成功**，也不是 80%。复杂草图基本修不动。
- **不会让模型更"合理"**，只是让草图从"没定死"变成"定死"。

## 实测数据

在一个真实的 60 文件模型库（260 个草图）上：

| 指标 | 初始 | 修复后 |
|---|---|---|
| 有问题的文件 | 39 | 35 |
| 欠定义草图 | **82** | **64** |

第一轮修复（前 8 个文件）修好了 18 个，效果明显；但**继续往下跑，成功率迅速归零**——说明先修好的那批是「好啃的」，剩下的都是硬骨头。

这些数字是实际跑出来的，不是估计。

## 快速开始

```bash
pip install pywin32
```

**先打开 SOLIDWORKS**（本工具不会替你启动它），然后：

```bash
python sw_health_check.py "D:\你的\模型目录"
```

| 参数 | 说明 |
|---|---|
| `target` | 要扫描的文件夹，或单个 SLDPRT/SLDASM 文件 |
| `--out 报告.html` | 生成 HTML 报告 |
| `--fix` | 尝试自动修复欠定义草图（先备份为 `.bak`） |
| `--limit N` | 只处理前 N 个 |
| `--skip N` | 跳过前 N 个（续跑用） |
| `--deep` | 控制台额外打印每个文件的明细 |

Windows 下也可以直接双击附带的 `.bat`。

## 判定依据

**草图约束状态用的是 `swConstrainedStatus_e` 的真实枚举值：**

| 值 | 含义 | 是否健康 |
|---|---|---|
| 1 | 未知状态 | ✗ |
| 2 | 欠定义 | ✗ |
| **3** | **完全定义** | **✓** |
| 4 | 过定义 | ✗ |
| 5 | 无解 | ✗ |
| 6 | 无效解 | ✗ |
| 7 | 自动求解已关闭 | ✗ |

⚠️ **注意不是 0。** 按「0 表示正常」的常见习惯写，会把所有草图都判成有问题，而且错得不明显。

## 已知限制

- **必须 SOLIDWORKS 运行中。** 走 COM 接口，没有 SW 就跑不了。
- **只处理 SW 原生格式。** STEP/IGES/X_T 需要先导入。
- **装配体只检查自身特征，不检查引用完整性。** 引用丢失、Toolbox 漏打包还没做。
- **速度约 6–8 秒/文件**，每个文件都要真实打开一次。
- **诊断里的「缺尺寸或定位」是推断**，不是 SW 给出的结论——SW 不提供自由度数量。
- **自动修复在复杂草图上会长时间计算**，所以设了 10 条图元的上限。

## 给开发者的踩坑记录

用 Python + pywin32 调 SOLIDWORKS COM，late binding 下一共踩了 8 个坑。

### 1. 部分方法按名字解析不到

`IModelDoc2.FirstFeature`、`IFeature.GetNextFeature` 会抛
`DISP_E_MEMBERNOTFOUND (0x80020003)`。必须用 DISPID 直调：

```python
doc._oleobj_.Invoke(65801, 0, pythoncom.DISPATCH_METHOD, True)  # FirstFeature
```

常用 DISPID（由 `SolidWorks.Interop.sldworks.dll` 反射取得）：

| 成员 | DISPID |
|---|---|
| `IModelDoc2.FirstFeature` | 65801 |
| `IModelDoc2.GetFeatureCount` | 66005 |
| `IFeature.GetNextFeature` | 3 |
| `IFeature.GetFirstSubFeature` | 20 |
| `IFeature.GetNextSubFeature` | 22 |
| `IFeature.IsSuppressed` | 26 |
| `IFeature.GetErrorCode` | 42 |
| `IFeature.GetSpecificFeature2` | 76 |
| `IFeature.GetTypeName2` | 103 |
| `IFeature.GetErrorCode2` | 104 |
| `ISketch.GetConstrainedStatus` | 48 |

### 2. 属性与方法标记不一致

`GetFeatureCount`、`GetTitle`、`GetTypeName2`、`IsSuppressed` 实际是**属性**。
写成 `doc.GetFeatureCount()` 会报 `'int' object is not callable`。

### 3. DISPID 直调返回裸 PyIDispatch

没有 `_oleobj_`，不能继续调成员，必须先包装：

```python
feat = win32com.client.Dispatch(
    doc._oleobj_.Invoke(65801, 0, pythoncom.DISPATCH_METHOD, True)
)
```

本项目的 `read()` 把前三条统一成一条路径：**属性 → 方法 → DISPID**，三路兜底。

### 4. 遍历特征树不要递归子特征

`GetNextFeature()` 本身已经是按树顺序的完整遍历（含嵌套特征）。
再递归 `GetFirstSubFeature()` 会把同一个草图算两遍。

### 5. `GetConstraints()` 对普通草图永远返回空

判断图元受约束程度要用 `ISketchSegment.GetRelationsCount()`，不是 `GetConstraints()`。

### 6. `AutoDimension2` 需要草图处于编辑状态

否则返回 `swAutodimStatus_e = 4`（没有活动草图）。进入编辑的正确姿势：

```python
feat.Select2(False, 0)   # 选中草图特征（两个参数，比 SelectByID2 稳）
doc.EditSketch()         # 进入草图
sketch.AutoDimension2(1, 1, 1, 1, 1)
doc.EditSketch()         # 退出
```

`SelectByID2` 在 late binding 下有参数类型问题，改用 `IFeature.Select2` 绕开。

### 7. 改完草图必须重建模型

`AutoDimension2` 之后如果不重建，几何没更新，读到的约束状态也不可信：

```python
doc.ForceRebuild3(True)
```

### 8. 可选 Object 参数不能传 `None`

`SelectByID2` 的 `Callout`、`FullyDefineSketch` 的 `DatumDisp` 传 `None` 会报
`DISP_E_TYPEMISMATCH`。传 `pythoncom.Empty`。

### 9. 查 API 签名不要解 CHM

`hh.exe -decompile` 解官方 CHM 会退出码 0 但不吐文件。改用 DLL 反射：

```powershell
$asm = [System.Reflection.Assembly]::LoadFrom("...\SolidWorks.Interop.sldworks.dll")
$asm.GetType("SolidWorks.Interop.sldworks.IFeature").GetMethods()
```

DISPID 用 `[System.Runtime.InteropServices.DispIdAttribute]` 读。

### 10. 复杂草图会让 AutoDimension2 卡上几分钟

实测 15 条图元的草图，`AutoDimension2` 算了约 9 分钟且没有任何进度提示。
COM 调用无法设超时，所以本项目直接按图元数量设上限跳过。

### 11. `IBody2.Check()` 的返回值不是错误码，要用 `Check2()`

对正常实体，`Check()` 一律返回 **1**（看起来像"实体损坏"），而 `Check2()` 返回 **0**。
误用 `Check()` 会把所有健康模型都判成损坏。

正确用法：

```python
code = body.Check2()      # 0 = 无错误，非 0 = swFaultEntityErrorCode_e
```

### 12. 导入 STEP 只能用 `LoadFile2`

同一份 STEP 文件，四种导入方式的结果：

| 接口 | 结果 |
|---|---|
| `OpenDoc6(step, 1, 1, ...)` | 返回 `None` |
| `LoadFile4(step, "", None, errors)` | `DISP_E_TYPEMISMATCH` |
| `LoadFile3(step, "", None)` | `DISP_E_TYPEMISMATCH` |
| **`LoadFile2(step, "")`** | **成功** |

导入成功后文档名会变成同名的 `.SLDPRT`（内存中，不落盘），用 `sw.ActiveDoc` 拿文档对象。

## 许可

MIT
