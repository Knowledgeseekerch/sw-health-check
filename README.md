# SOLIDWORKS 模型体检 (sw-health-check)

扫描一批 SOLIDWORKS 模型，查出**欠定义草图**、**报错特征**、**被压缩特征**，**并自动修好其中大部分**。

> 定位：**通用的模型质量门禁**。不绑定任何建模工具——不管模型是 AI 生成的、外协发来的、网上下载的、还是自己几个月前画的，只要进了 SOLIDWORKS，它都能检查。

## 解决什么问题

模型能打开、能旋转、看起来没毛病，但：

- 草图是蓝色的（欠定义），一动就跑偏
- 特征树里有黄色感叹号，重建就报错
- 拿到别人的模型，不知道里面埋了什么

这些问题的共同点是：**SW 不会主动告诉你，只有出事了才发现。**

## 两件事

### 1. 查

```bash
python sw_health_check.py "D:\你的\模型目录"
```

逐个打开、遍历完整特征树、检查每个草图和特征，输出一份 HTML 报告。

### 2. 修

```bash
python sw_health_check.py "D:\你的\模型目录" --fix
```

对欠定义草图调用 SOLIDWORKS 自带的「自动标注尺寸」，把草图从欠定义修成完全定义，并写回文件。

## 实测数据

在 60 个真实模型（260 个草图）上跑出来的结果：

| 指标 | 数值 |
|---|---|
| 扫描文件 | 60 |
| 有问题的文件 | 39（65%） |
| 欠定义草图 | 82 |
| ├ 缺尺寸或定位 | 41 |
| ├ 有图元无约束 | 37 |
| └ 空草图 | 4 |
| **自动修复成功率** | **13/16（81%）** |

这些数字是实际跑出来的，不是估计。

## 输出示例

```
[欠定义] 草图2：欠定义｜6 条图元（直线 3、圆弧 3），全部已有几何关系，缺的通常是尺寸或定位｜已自动修复为完全定义
[欠定义] 草图8：欠定义｜1 条图元（圆弧 1），其中 1 条没有任何几何关系（圆弧1），建议优先检查这几条｜已自动修复为完全定义
[欠定义] 草图4：欠定义｜草图里没有可识别的图元（可能只包含点或参考线）｜自动修复未成功（草图是空的）
```

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
| `--limit N` | 只处理前 N 个（先小范围试） |
| `--deep` | 控制台额外打印每个文件的明细 |

## 自动修复的安全设计

改别人的模型文件是危险操作，所以：

1. **默认不修复**，必须显式加 `--fix`
2. **修复前自动备份**为同目录的 `.bak`（保留原始时间戳）
3. **只在「修好了且没变糟」时存盘**——如果某个草图修复后变成「过定义」，整个文件跳过不保存
4. **备份失败就跳过该文件**，不冒险

### 还原方法

删掉修复后的 `.SLDPRT`，把同名 `.SLDPRT.bak` 改回 `.SLDPRT` 即可。

### 修复不保证什么

- **只修「欠定义」这个状态，不保证标注好看。** 自动加的尺寸可能堆在一起，修完你大概率还要手动拖一下位置。
- **空草图修不了**（没有图元可标注）——这类草图本来就该删。
- **不是 100% 成功。** 复杂草图 AutoDimension2 可能算不出解，工具会如实报告"未成功"。

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
- **只处理 SW 原生格式。** STEP/IGES/X_T 需要先导入 SW（后续计划支持）。
- **装配体只检查自身特征，不检查引用完整性。** 引用丢失、Toolbox 漏打包还没做。
- **速度约 6–8 秒/文件**，每个文件都要真实打开一次。
- **诊断里的「缺尺寸或定位」是推断**，不是 SW 给出的结论——SW 不提供自由度数量。

## 给开发者的踩坑记录

用 Python + pywin32 调 SOLIDWORKS COM，late binding 下一共踩了 8 个坑，全部记录在这里。

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

### 7. 可选 Object 参数不能传 `None`

`SelectByID2` 的 `Callout`、`FullyDefineSketch` 的 `DatumDisp` 传 `None` 会报
`DISP_E_TYPEMISMATCH`。传 `pythoncom.Empty`。

### 8. 查 API 签名不要解 CHM

`hh.exe -decompile` 解官方 CHM 会退出码 0 但不吐文件。改用 DLL 反射：

```powershell
$asm = [System.Reflection.Assembly]::LoadFrom("...\SolidWorks.Interop.sldworks.dll")
$asm.GetType("SolidWorks.Interop.sldworks.IFeature").GetMethods()
```

DISPID 用 `[System.Runtime.InteropServices.DispIdAttribute]` 读。

## 许可

MIT
