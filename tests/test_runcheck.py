import json
from pathlib import Path

from app.core.runcheck import check_run


def _entry(target, title, series, idx, authors, info=None, result="done"):
    read = {"book info.txt": info} if info else {}
    return {"book": "x", "read": read, "result": result, "detail": "",
            "decided": {"values": {"title": title, "series": series, "series_index": idx, "authors": authors},
                        "from": {}, "kind": "audio (light novel)", "status": "ready", "target": target}}


def test_check_run_finds_split_series_cjk_and_swaps(tmp_path):
    root = "H:/x/AudioBooks Sorted/Light Novels"
    entries = [
        _entry(f"{root}/Rarutori/Enough with This Slow Life! I Was/1 - A", "Enough, Vol. 1", "Enough", 1, ["Rarutori"]),
        _entry(f"{root}/Rarutori/Enough with This Slow Life! I Was Bored/2 - B", "Enough, Vol. 2", "Enough", 2, ["Rarutori"]),
        _entry(f"{root}/猫子/Disciple/1 - C", "Disciple, Vol. 1", "Disciple", 1, ["猫子"]),
        _entry(f"{root}/Brian/How Do You Live", "How Do You Live?", None, None, ["Brian Nishii"],
               info={"authors": ["Genzaburo Yoshino"], "narrators": ["Brian Nishii"]}),
        _entry(f"{root}/Good/Fine/1 - Fine, Vol. 1", "Fine, Vol. 1", "Fine", 1, ["Good"]),
    ]
    d = tmp_path / "history" / "info"
    d.mkdir(parents=True)
    (d / "r1.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
    total, found = check_run(tmp_path, "r1")
    by_name = {Path(f.target).name: f.problems for f in found}
    assert total == 5 and len(found) == 4 and "1 - Fine, Vol. 1" not in by_name
    assert any("more than one series folder" in p for p in by_name["1 - A"])
    assert any("Japanese/Chinese" in p for p in by_name["1 - C"])
    assert any("swapped" in p for p in by_name["How Do You Live"])
