"""批量导入：递归扫描子目录 + 自动匹配封面 的回归测试。

纯 Python（不依赖浏览器），覆盖：
  - match_cover_for_video 的匹配优先级
  - scan_import_dir 递归/非递归、封面匹配、重复标记
  - import_paths(with_cover=True) 把封面写进素材
  - skip_duplicates 去重
不污染真实 state/materials.json（临时账号数据用完即清）。
"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wechannels import materials as M
from wechannels import accounts, config

PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def _write(path, is_binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if is_binary else "w") as f:
        f.write(b"x" if is_binary else "x")


def test_match_cover():
    print("[match_cover_for_video]")
    tmp = tempfile.mkdtemp()
    try:
        def mk(folder, files):
            d = os.path.join(tmp, folder)
            os.makedirs(d, exist_ok=True)
            for fn in files:
                _write(os.path.join(d, fn), is_binary=fn.lower().endswith((".mp4", ".jpg", ".png")))
            return d

        # 1) 同名-封面 优先于其它图
        d1 = mk("c1", ["A.mp4", "A-封面.jpg", "A花絮.jpg"])
        ck("同名-封面优先", os.path.basename(M.match_cover_for_video(os.path.join(d1, "A.mp4"))) == "A-封面.jpg")
        # 2) 含「封面」字样（不同名）
        d2 = mk("c2", ["B.mp4", "随便-封面.png", "B图.jpg"])
        ck("含封面字样", os.path.basename(M.match_cover_for_video(os.path.join(d2, "B.mp4"))) == "随便-封面.png")
        # 3) 唯一一张图兜底
        d3 = mk("c3", ["C.mp4", "唯一图.jpg"])
        ck("唯一图兜底", os.path.basename(M.match_cover_for_video(os.path.join(d3, "C.mp4"))) == "唯一图.jpg")
        # 4) 多图且无封面字样 -> 不匹配
        d4 = mk("c4", ["D.mp4", "D1.jpg", "D2.jpg"])
        ck("多图无封面不匹配", M.match_cover_for_video(os.path.join(d4, "D.mp4")) == "")
        # 5) 无图
        d5 = mk("c5", ["E.mp4"])
        ck("无图返回空", M.match_cover_for_video(os.path.join(d5, "E.mp4")) == "")
    finally:
        shutil.rmtree(tmp)


def test_scan_recursive():
    print("[scan_import_dir]")
    tmp = tempfile.mkdtemp()
    try:
        # 结构：root/子A/视频+封面, root/子B/视频+封面, root/顶层.mp4
        mkA = os.path.join(tmp, "子A"); os.makedirs(mkA)
        _write(os.path.join(mkA, "a.mp4"), True)
        _write(os.path.join(mkA, "a-封面.jpg"), True)
        mkB = os.path.join(tmp, "子B"); os.makedirs(mkB)
        _write(os.path.join(mkB, "b.mp4"), True)
        _write(os.path.join(mkB, "b-封面.jpg"), True)
        _write(os.path.join(tmp, "top.mp4"), True)

        rec = M.scan_import_dir(tmp, recursive=True, skip_duplicates=False)
        ck("递归找到 3 个视频", rec["total"] == 3, f"got {rec['total']}")
        ck("递归匹配到 2 个封面", sum(1 for g in rec["groups"] if g["cover_path"]) == 2)
        names = {os.path.basename(g["video_path"]) for g in rec["groups"]}
        ck("含子目录视频", {"a.mp4", "b.mp4", "top.mp4"} <= names)

        non = M.scan_import_dir(tmp, recursive=False, skip_duplicates=False)
        ck("非递归只找到顶层 1 个", non["total"] == 1, f"got {non['total']}")
    finally:
        shutil.rmtree(tmp)


def test_import_with_cover():
    print("[import_paths with_cover]")
    tmp = tempfile.mkdtemp()
    try:
        sd = os.path.join(tmp, "款式1"); os.makedirs(sd)
        vpath = os.path.join(sd, "x.mp4"); _write(vpath, True)
        cpath = os.path.join(sd, "x-封面.jpg"); _write(cpath, True)

        # 用临时账号隔离，避免污染真实库
        aid = "test_import_recursive_acc"
        r = M.import_paths([vpath], account_id=aid, skip_duplicates=False, with_cover=True)
        ck("导入 1 条", r["added"] == 1, str(r))
        mats = M.load_materials(aid)
        ck("素材存在", len(mats) == 1)
        ck("封面已写入", mats and mats[0].get("cover_path") == cpath,
           f"got {mats[0].get('cover_path') if mats else None}")

        # 去重：再导一次应跳过
        r2 = M.import_paths([vpath], account_id=aid, skip_duplicates=True, with_cover=True)
        ck("去重跳过", r2["added"] == 0 and r2["skipped"] == 1, str(r2))

        # 清理临时账号数据
        items = M.load_materials()
        M.save_materials([m for m in items if m.get("account_id") != aid])
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_match_cover()
    test_scan_recursive()
    test_import_with_cover()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
