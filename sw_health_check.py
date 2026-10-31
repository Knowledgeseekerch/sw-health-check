# -*- coding: utf-8 -*-
"""
SOLIDWORKS 模型体检 (Model Health Check)
=========================================

扫描一个文件夹（或单个文件）里的 SLDPRT / SLDASM，逐个打开检查，找出：

  - 未完全定义 / 过定义 / 无解的草图
  - 报错的特征
  - 被压缩的特征

最后输出一份报告（控制台 + HTML）。

用法
----
    python sw_health_check.py "D:\\你的\\模型目录"
    python sw_health_check.py "D:\\某个文件.SLDPRT"
    python sw_health_check.py "D:\\模型目录" --out 报告.html
    python sw_health_check.py "D:\\模型目录" --limit 10      # 只测前 10 个

依赖
----
    pip install pywin32
    SOLIDWORKS 必须处于运行状态（本工具不会替你启动它）。

关于 late binding
-----------------
SOLIDWORKS 的 COM 接口在 Python late-binding 下有三个坑，本文件逐一处理：

1. 部分方法按名字解析不到（DISP_E_MEMBERNOTFOUND），典型如
   IModelDoc2.FirstFeature、IFeature.GetNextFeature，必须用 DISPID 直调。
2. 属性与方法标记不一致：GetFeatureCount / GetTitle / GetTypeName2 /
   IsSuppressed 实际是"属性"，写成方法调用会报 not callable。
3. DISPID 直调返回的是裸 PyIDispatch，没有 _oleobj_，必须先经
   win32com.client.Dispatch 包装才能继续调成员。

因此所有读取统一走 read()：属性 → 方法 → DISPID 三路兜底。
"""

import argparse
import os
import shutil
import sys
import time
import traceback
from html import escape

import pythoncom
import win32com.client


# --------------------------------------------------------------------------
# SOLIDWORKS 常量
# --------------------------------------------------------------------------

swDocPART = 1
swDocASSEMBLY = 2

swOpenDocOptions_Silent = 1
swOpenDocOptions_ReadOnly = 2

# 草图约束状态 -> 说明 (swConstrainedStatus_e)
# 注意：3 才是"完全定义"，不是 0
CONSTRAINT_STATUS = {
    1: "未知状态",
    2: "欠定义",
    3: "完全定义",
    4: "过定义",
    5: "无解",
    6: "无效解",
    7: "自动求解已关闭",
}

# 需要报告出来的状态（3 = 完全定义，是唯一健康态）
BAD_CONSTRAINT = {2, 4, 5, 6, 7}

# 自动标注尺寸的返回码 (swAutodimStatus_e)
AUTODIM_STATUS = {
    0: "成功", 1: "选项无效", 2: "无活动文档", 3: "文档类型不支持",
    4: "没有活动草图", 5: "不支持 3D 草图", 6: "草图是空的",
    7: "草图过定义", 8: "没有实体", 9: "实体无效", 10: "不允许中心线",
    11: "未提供基准", 12: "基准不唯一", 13: "基准类型无效",
    14: "基准线不是中心线", 15: "基准线不垂直", 16: "基准线不水平",
    17: "算法失败", 18: "找不到解",
}

# 特征错误码 -> 说明 (swFeatureError_e)，只列常见的，其余显示数字
FEATURE_ERRORS = {
    1: "未知错误",
    10: "圆角：找不到环",
    11: "圆角：找不到面",
    12: "圆角：半径无效",
    13: "圆角：找不到边",
    14: "圆角：模型几何有问题",
    15: "圆角：半径太小",
    16: "圆角：无法延伸",
    17: "圆角：半径会消除元素",
    18: "圆角：半径太大",
    19: "圆角：半径太大（超出邻接面）",
    30: "拉伸：结果不相交",
    31: "拉伸：找不到终止面",
    32: "拉伸：几何条件不良",
    33: "拉伸切除：既有开放又有封闭轮廓",
    34: "拉伸切除：轮廓无效",
    35: "开放切除：轮廓无效",
    36: "拉伸凸台：既有开放又有封闭轮廓",
    37: "拉伸凸台：轮廓无效",
    38: "配合：无效的边",
    39: "配合：无效的面",
    40: "配合：创建曲面失败",
    41: "配合：无效的实体",
    42: "配合：未知的相切关系",
    43: "配合：几何悬挂",
    44: "配合：实体不是线性的",
    45: "配合：实体操作失败",
    46: "配合：过定义",
    47: "配合：定义不良",
    48: "配合：已断裂",
    49: "特征：已弃用",
    50: "特征：已过时",
    51: "草图：外部参考失败",
}

# DISPID（由 SolidWorks.Interop.sldworks.dll 反射取得）
DISPID = {
    "FirstFeature": 65801,
    "GetNextFeature": 3,
    "GetFirstSubFeature": 20,
    "GetNextSubFeature": 22,
    "IsSuppressed": 26,
    "GetErrorCode": 42,
    "GetSpecificFeature2": 76,
    "GetTypeName2": 103,
    "GetErrorCode2": 104,
    "GetConstrainedStatus": 48,
    "Name": 1,
}

SKETCH_TYPES = ("ProfileFeature", "Sketch")

# 草图图元类型 (swSketchSegments_e)
SEG_TYPE = {0: "直线", 1: "圆弧", 2: "椭圆", 3: "样条", 4: "文本", 5: "抛物线"}


def diagnose_sketch(sketch):
    """对一个草图做深度诊断：统计图元数量、类型，以及完全没有几何关系的图元。

    判定口径来自实测：
      - 欠定义草图中，关系数 = 0 的图元几乎必然未定形
      - 若所有图元都有几何关系但整体仍欠定义，缺的通常是尺寸或定位
    """
    info = {"segments": 0, "types": {}, "no_relation": 0, "names": []}
    try:
        segs = read(sketch, "GetSketchSegments")
    except Exception:
        return info

    if segs is None:
        segs = []
    if not isinstance(segs, (tuple, list)):
        segs = [segs]

    for s in segs:
        s = wrap(s)
        info["segments"] += 1

        try:
            st = read(s, "GetType")
        except Exception:
            st = -1
        info["types"][st] = info["types"].get(st, 0) + 1

        try:
            nrel = read(s, "GetRelationsCount") or 0
        except Exception:
            nrel = 0
        if not nrel:
            info["no_relation"] += 1
            try:
                info["names"].append(read(s, "GetName") or "图元")
            except Exception:
                info["names"].append("图元")

    return info


def describe_sketch(info):
    """把诊断结果翻成一句人能看懂的判断。"""
    if not info or not info["segments"]:
        return "草图里没有可识别的图元（可能只包含点或参考线）"

    type_desc = "、".join(
        f"{SEG_TYPE.get(k, '其他')} {v}" for k, v in sorted(info["types"].items())
    )
    head = f"{info['segments']} 条图元（{type_desc}）"

    if info["no_relation"]:
        names = "、".join(info["names"][:5])
        more = " 等" if len(info["names"]) > 5 else ""
        return (
            f"{head}，其中 {info['no_relation']} 条没有任何几何关系"
            f"（{names}{more}），建议优先检查这几条"
        )

    return f"{head}，全部已有几何关系，缺的通常是尺寸或定位"


def try_fix_sketch(doc, feat, sketch):
    """用 SW 自带的「自动标注尺寸」修复一个欠定义草图。

    流程：选中草图特征 → 进入草图编辑 → AutoDimension2 → 退出。

    返回 (修复前状态, 修复后状态, 说明)。
    注意：本函数只改内存中的模型，是否存盘由调用方决定。
    """
    try:
        before = read(sketch, "GetConstrainedStatus")
    except Exception:
        return None, None, "读不到约束状态"

    def _leave():
        try:
            doc.EditSketch()
        except Exception:
            pass
        try:
            doc.ClearSelection2(True)
        except Exception:
            pass

    rc = None
    try:
        if not read(feat, "Select2", False, 0):
            return before, before, "选中特征失败"
        doc.EditSketch()
        try:
            rc = read(sketch, "AutoDimension2", 1, 1, 1, 1, 1)
        finally:
            _leave()
        after = read(sketch, "GetConstrainedStatus")
        return before, after, AUTODIM_STATUS.get(rc, f"返回码 {rc}")
    except Exception as e:
        _leave()
        try:
            return before, read(sketch, "GetConstrainedStatus"), f"出错：{e}"
        except Exception:
            return before, before, f"出错：{e}"


# --------------------------------------------------------------------------
# COM 调用适配层
# --------------------------------------------------------------------------

def wrap(obj):
    """把裸 PyIDispatch 包装成可继续调用成员的对象。"""
    if obj is None:
        return None
    if isinstance(obj, win32com.client.CDispatch):
        return obj
    try:
        return win32com.client.Dispatch(obj)
    except Exception:
        return obj


def read(obj, name, *args):
    """读取 SW 成员的统一入口：属性 → 方法 → DISPID 直调。

    args 非空时视为方法参数（如 GetErrorCode2 的 ref bool）。
    """
    # 1) 当作属性 / 方法
    try:
        v = getattr(obj, name)
        if not callable(v):
            return v
        try:
            return v(*args)
        except TypeError:
            pass
    except Exception:
        pass

    # 2) DISPID 直调
    dispid = DISPID.get(name)
    if dispid is None:
        raise RuntimeError(f"读不到成员 {name}（没有 DISPID 可兜底）")

    ole = getattr(obj, "_oleobj_", None)
    if ole is None:
        ole = obj
    if not hasattr(ole, "Invoke"):
        raise RuntimeError(f"读不到成员 {name}（对象无法 Invoke）")

    err = None
    for flags in (pythoncom.DISPATCH_METHOD, pythoncom.DISPATCH_PROPERTYGET):
        try:
            return ole.Invoke(dispid, 0, flags, True, *args)
        except Exception as e:
            err = e
    raise err


def byref_int(value=0):
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, value)


def byref_bool(value=False):
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, value)


# --------------------------------------------------------------------------
# 连接 SW 与文件收集
# --------------------------------------------------------------------------

def connect_sw():
    """拿到正在运行的 SOLIDWORKS 实例，没有就直接退出。"""
    try:
        sw = win32com.client.GetActiveObject("SldWorks.Application")
    except Exception:
        print()
        print("  [错误] 没有找到正在运行的 SOLIDWORKS。")
        print("         请先打开 SOLIDWORKS，再运行本工具。")
        print()
        sys.exit(1)

    try:
        rev = sw.RevisionNumber
        print(f"  已连接 SOLIDWORKS {rev}")
    except Exception:
        print("  已连接 SOLIDWORKS（版本号读取失败，不影响检查）")
    return sw


def collect_files(target):
    target = os.path.abspath(target)
    if os.path.isfile(target):
        return [target]
    if not os.path.isdir(target):
        print(f"  [错误] 路径不存在：{target}")
        sys.exit(1)

    found = []
    for root, _dirs, names in os.walk(target):
        for n in names:
            if n.lower().endswith((".sldprt", ".sldasm")):
                found.append(os.path.join(root, n))
    found.sort()
    return found


# --------------------------------------------------------------------------
# 检查单个文件
# --------------------------------------------------------------------------

def inspect_file(sw, path, do_fix=False):
    """打开一个文件并检查，返回结果字典。

    do_fix=True 时会对欠定义草图尝试自动修复，并在有改善时存盘
    （备份由调用方负责）。
    """
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    is_asm = ext == ".sldasm"
    doc_type = swDocASSEMBLY if is_asm else swDocPART

    result = {
        "path": path,
        "name": name,
        "type": "装配" if is_asm else "零件",
        "opened": False,
        "error": None,
        "feature_count": 0,
        "sketch_count": 0,
        "issues": [],
        "elapsed": 0.0,
        "fixed_count": 0,
        "saved": False,
        "made_worse": False,
    }

    t0 = time.time()
    doc = None
    try:
        errors = byref_int(0)
        warnings = byref_int(0)
        doc = sw.OpenDoc6(
            path,
            doc_type,
            (swOpenDocOptions_Silent if do_fix
             else swOpenDocOptions_Silent | swOpenDocOptions_ReadOnly),
            "",
            errors,
            warnings,
        )
    except Exception as e:
        result["error"] = f"打开失败：{e}"
        result["elapsed"] = time.time() - t0
        return result

    if doc is None:
        err_code = None
        try:
            err_code = errors.value
        except Exception:
            pass
        result["error"] = f"打开失败（SW 返回空，错误码 {err_code}）"
        result["elapsed"] = time.time() - t0
        return result

    result["opened"] = True
    title = None

    try:
        title = read(doc, "GetTitle")

        feat = wrap(read(doc, "FirstFeature"))
        seen = set()
        count = 0

        # GetNextFeature 已经是按树顺序的完整遍历（含嵌套特征），
        # 不要再额外递归子特征，否则同一个草图会被计两次。
        while feat is not None:
            count += 1
            if count > 20000:          # 防御：异常模型不要死循环
                break

            try:
                feat_name = read(feat, "Name") or "(未命名)"
            except Exception:
                feat_name = "(读取失败)"
            try:
                type_name = read(feat, "GetTypeName2") or ""
            except Exception:
                type_name = ""

            # 同名同类型只算一次（草图既在顶层出现，也作为拉伸的子特征出现）
            key = (feat_name, type_name)
            if key in seen:
                try:
                    feat = wrap(read(feat, "GetNextFeature"))
                except Exception:
                    break
                continue
            seen.add(key)

            # --- 压缩 ---
            try:
                if read(feat, "IsSuppressed"):
                    result["issues"].append({
                        "kind": "suppressed",
                        "feature": feat_name,
                        "detail": "特征被压缩（不参与建模，但可能不是本意）",
                    })
            except Exception:
                pass

            # --- 报错 ---
            err_code = None
            is_warning = False
            try:
                warn_flag = byref_bool(False)
                err_code = read(feat, "GetErrorCode2", warn_flag)
                try:
                    is_warning = bool(warn_flag.value)
                except Exception:
                    is_warning = False
            except Exception:
                try:
                    err_code = read(feat, "GetErrorCode")
                except Exception:
                    err_code = None

            if err_code:
                detail = FEATURE_ERRORS.get(err_code, f"错误码 {err_code}")
                result["issues"].append({
                    "kind": "warning" if is_warning else "error",
                    "feature": feat_name,
                    "detail": ("警告：" if is_warning else "错误：") + detail,
                })

            # --- 草图 ---
            if type_name in SKETCH_TYPES:
                result["sketch_count"] += 1
                sketch = None
                try:
                    sketch = wrap(read(feat, "GetSpecificFeature2"))
                    status = read(sketch, "GetConstrainedStatus")
                except Exception:
                    status = None

                if status in BAD_CONSTRAINT:
                    base = CONSTRAINT_STATUS.get(status, f"状态 {status}")
                    diag = ""
                    if sketch is not None and status == 2:
                        diag = describe_sketch(diagnose_sketch(sketch))
                    detail = base + ("｜" + diag if diag else "")

                    if do_fix and sketch is not None and status == 2:
                        _b, after, note = try_fix_sketch(doc, feat, sketch)
                        if after == 3:
                            detail += "｜已自动修复为完全定义"
                            result["fixed_count"] += 1
                        elif after in (4, 5, 6):
                            detail += (f"｜自动修复后变成「"
                                       f"{CONSTRAINT_STATUS.get(after, after)}」，不保存")
                            result["made_worse"] = True
                        elif after == 2:
                            if note == "成功":
                                detail += "｜自动标注返回成功，但草图仍未定形"
                            else:
                                detail += f"｜自动修复未成功（{note}）"
                        else:
                            detail += f"｜修复后状态 {after}（{note}）"

                    result["issues"].append({
                        "kind": "underdefined" if status == 2 else "constraint",
                        "feature": feat_name,
                        "detail": detail,
                    })

            try:
                feat = wrap(read(feat, "GetNextFeature"))
            except Exception:
                break

        result["feature_count"] = count

    except Exception:
        result["error"] = "检查过程中出错：\n" + traceback.format_exc()
    finally:
        try:
            if title:
                if do_fix and result["fixed_count"] and not result["made_worse"]:
                    try:
                        doc.Save()
                        result["saved"] = True
                    except Exception as e:
                        result["error"] = f"保存失败：{e}"
                sw.CloseDoc(title)
        except Exception:
            pass

    result["elapsed"] = time.time() - t0
    return result


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

KIND_LABEL = {
    "underdefined": "欠定义",
    "constraint": "约束异常",
    "error": "特征报错",
    "warning": "特征警告",
    "suppressed": "被压缩",
}


def render_console(results, deep=False):
    total = len(results)
    opened = sum(1 for r in results if r["opened"])
    failed = total - opened
    with_issues = sum(1 for r in results if r["issues"])
    n_issues = sum(len(r["issues"]) for r in results)
    n_sketch = sum(r["sketch_count"] for r in results)
    n_under = sum(1 for r in results for i in r["issues"] if i["kind"] == "underdefined")

    print()
    print("=" * 70)
    print("  体检结果")
    print("=" * 70)
    print(f"  扫描文件      : {total}")
    print(f"  成功打开      : {opened}")
    if failed:
        print(f"  打开失败      : {failed}")
    print(f"  检查到的草图  : {n_sketch}")
    print(f"  有问题的文件  : {with_issues}")
    print(f"  问题总数      : {n_issues}   （其中欠定义草图 {n_under} 个）")
    print("=" * 70)

    if with_issues:
        print()
        print("  有问题的文件：")
        print()
        for r in results:
            if not r["issues"]:
                continue
            print(f"  - {r['name']}  [{r['type']}]")
            for i in r["issues"]:
                label = KIND_LABEL.get(i["kind"], i["kind"])
                print(f"      [{label}] {i['feature']}：{i['detail']}")
            print()

    if failed:
        print("  打不开的文件：")
        print()
        for r in results:
            if not r["opened"]:
                print(f"  - {r['name']}：{r['error']}")
        print()

    if deep:
        print("  全部文件明细：")
        print()
        for r in results:
            flag = "OK" if (r["opened"] and not r["issues"]) else "--"
            print(f"  [{flag}] {r['name']:<40} 特征 {r['feature_count']:>3}  "
                  f"草图 {r['sketch_count']:>3}  问题 {len(r['issues']):>2}  "
                  f"{r['elapsed']:.1f}s")
        print()


def render_html(results, out_path):
    total = len(results)
    opened = sum(1 for r in results if r["opened"])
    with_issues = sum(1 for r in results if r["issues"])
    n_issues = sum(len(r["issues"]) for r in results)
    n_sketch = sum(r["sketch_count"] for r in results)
    n_under = sum(1 for r in results for i in r["issues"] if i["kind"] == "underdefined")

    rows = []
    for r in sorted(results, key=lambda x: (len(x["issues"]) == 0, x["name"].lower())):
        kinds = sorted({KIND_LABEL.get(i["kind"], i["kind"]) for i in r["issues"]})
        if not r["opened"]:
            status = '<span class="bad">打不开</span>'
        elif r["issues"]:
            status = f'<span class="warn">{escape(" / ".join(kinds))}</span>'
        else:
            status = '<span class="ok">正常</span>'

        detail = ""
        if r["issues"]:
            items = "".join(
                f'<li><b>{escape(KIND_LABEL.get(i["kind"], i["kind"]))}</b> '
                f'{escape(str(i["feature"]))}：{escape(str(i["detail"]))}</li>'
                for i in r["issues"]
            )
            detail = f'<ul class="issues">{items}</ul>'
        if r["error"]:
            detail += f'<pre class="err">{escape(str(r["error"]))}</pre>'

        rows.append(f"""
        <tr>
          <td class="fname">{escape(r["name"])}</td>
          <td>{escape(r["type"])}</td>
          <td class="num">{r["feature_count"]}</td>
          <td class="num">{r["sketch_count"]}</td>
          <td class="num">{len(r["issues"])}</td>
          <td>{status}{detail}</td>
        </tr>""")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOLIDWORKS 模型体检报告</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 24px 64px;
    font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
    background: #f6f7f9; color: #1c1e21;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16181c; color: #e6e8eb; }}
    .card {{ background: #1f2226 !important; border-color: #2c3036 !important; }}
    th {{ background: #24282d !important; }}
    tr:nth-child(even) td {{ background: #1b1e22 !important; }}
    .fname {{ color: #8ab4f8 !important; }}
    .stat {{ background: #1f2226 !important; border-color: #2c3036 !important; }}
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; font-weight: 650; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 24px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
  .stat {{
    background: #fff; border: 1px solid #e3e6ea; border-radius: 10px;
    padding: 12px 18px; min-width: 110px;
  }}
  .stat .n {{ font-size: 22px; font-weight: 650; line-height: 1.2; }}
  .stat .l {{ font-size: 12px; color: #6b7280; }}
  .stat.hot .n {{ color: #d93025; }}
  .card {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 10px; overflow: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 9px 12px; text-align: left; vertical-align: top;
            border-bottom: 1px solid #eceff2; }}
  th {{ background: #fafbfc; font-weight: 600; position: sticky; top: 0;
        white-space: nowrap; }}
  td:nth-child(2) {{ white-space: nowrap; }}
  tr:nth-child(even) td {{ background: #fbfcfd; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .fname {{ font-weight: 550; color: #1a56db; word-break: break-all; }}
  .ok {{ color: #137333; }}
  .warn {{ color: #b06000; font-weight: 550; }}
  .bad {{ color: #d93025; font-weight: 550; }}
  .issues {{ margin: 6px 0 0; padding-left: 18px; color: #4b5563; }}
  .issues li {{ margin: 2px 0; }}
  .err {{ margin: 8px 0 0; padding: 8px; background: #fef2f2; border-radius: 6px;
          color: #991b1b; font-size: 12px; white-space: pre-wrap; }}
  .foot {{ margin-top: 20px; color: #9ca3af; font-size: 12px; }}
</style>
</head>
<body>
  <h1>SOLIDWORKS 模型体检报告</h1>
  <div class="sub">生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')} · 共扫描 {total} 个文件</div>

  <div class="stats">
    <div class="stat"><div class="n">{total}</div><div class="l">扫描文件</div></div>
    <div class="stat"><div class="n">{opened}</div><div class="l">成功打开</div></div>
    <div class="stat"><div class="n">{with_issues}</div><div class="l">有问题的文件</div></div>
    <div class="stat"><div class="n">{n_issues}</div><div class="l">问题总数</div></div>
    <div class="stat hot"><div class="n">{n_under}</div><div class="l">欠定义草图</div></div>
    <div class="stat"><div class="n">{n_sketch}</div><div class="l">检查到的草图</div></div>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr><th>文件名</th><th>类型</th><th class="num">特征</th><th class="num">草图</th><th class="num">问题</th><th>状态 / 详情</th></tr>
      </thead>
      <tbody>{''.join(rows)}
      </tbody>
    </table>
  </div>

  <div class="foot">
    判定依据：草图约束状态 = 3 视为完全定义（2 欠定义 / 4 过定义 / 5 无解 / 6 无效解 / 7 自动求解关闭）。
    由 sw_health_check.py 生成。
  </div>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SOLIDWORKS 模型体检：找出欠定义草图、报错特征、被压缩特征",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="要扫描的文件夹或单个 SLDPRT/SLDASM 文件")
    parser.add_argument("--out", default="", help="HTML 报告输出路径（默认不生成）")
    parser.add_argument("--limit", type=int, default=0, help="最多检查几个文件（调试用）")
    parser.add_argument("--deep", action="store_true", help="控制台额外打印每个文件的明细")
    parser.add_argument(
        "--fix", action="store_true",
        help="尝试自动修复欠定义草图（先备份为 .bak；仅在修好且未变糟时存盘）",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print()
    print("=" * 70)
    print("  SOLIDWORKS 模型体检")
    print("=" * 70)

    sw = connect_sw()

    files = collect_files(args.target)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"  [提示] 这个路径下没有找到 .sldprt / .sldasm 文件：{args.target}")
        return

    print(f"  待检查 {len(files)} 个文件，逐个打开中……（不要操作 SOLIDWORKS）")
    print()

    results = []
    t_start = time.time()
    for idx, path in enumerate(files, 1):
        name = os.path.basename(path)
        print(f"  [{idx}/{len(files)}] {name} ... ", end="", flush=True)

        if args.fix:
            try:
                shutil.copy2(path, path + ".bak")
            except Exception as e:
                print(f"备份失败，跳过（{e}）")
                continue

        try:
            r = inspect_file(sw, path, do_fix=args.fix)
        except Exception:
            r = {
                "path": path, "name": name, "type": "零件", "opened": False,
                "error": traceback.format_exc(), "feature_count": 0,
                "sketch_count": 0, "issues": [], "elapsed": 0.0,
                "fixed_count": 0, "saved": False, "made_worse": False,
            }
        results.append(r)

        if not r["opened"]:
            print("打不开")
        elif args.fix and r.get("fixed_count"):
            tail = "已保存" if r.get("saved") else "未保存"
            print(f"修复 {r['fixed_count']} 个草图（{tail}）")
        elif r["issues"]:
            print(f"{len(r['issues'])} 个问题")
        else:
            print("OK")

    elapsed = time.time() - t_start

    render_console(results, deep=args.deep)

    if args.fix:
        n_fixed = sum(r.get("fixed_count", 0) for r in results)
        n_saved = sum(1 for r in results if r.get("saved"))
        n_worse = sum(1 for r in results if r.get("made_worse"))
        print()
        print("=" * 70)
        print("  自动修复结果")
        print("=" * 70)
        print(f"  修复成功的草图  : {n_fixed}")
        print(f"  已存盘的文件    : {n_saved}")
        if n_worse:
            print(f"  修复后变糟的文件: {n_worse}（已跳过，未保存）")
        print("  原始文件备份    : 同目录下的 *.bak")
        print("=" * 70)

    print(f"  耗时 {elapsed:.1f} 秒")
    print()

    if args.out:
        out_path = os.path.abspath(args.out)
        try:
            render_html(results, out_path)
            print(f"  HTML 报告已写入：{out_path}")
        except Exception as e:
            print(f"  [警告] HTML 报告写入失败：{e}")
        print()


if __name__ == "__main__":
    main()
