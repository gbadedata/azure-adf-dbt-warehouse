from pathlib import Path

from ingest.sql_store import split_batches

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def test_go_splits_batches():
    script = "SELECT 1;\nGO\n\n  go  \nSELECT 2;\nGO\n"
    assert split_batches(script) == ["SELECT 1;", "SELECT 2;"]


def test_every_create_is_guarded_so_scripts_can_be_rerun():
    for path in SQL_DIR.glob("*.sql"):
        for batch in split_batches(path.read_text(encoding="utf-8")):
            lines = [line for line in batch.splitlines() if line.strip() and not line.strip().startswith("--")]
            if any(line.lstrip().upper().startswith("CREATE ") for line in lines):
                assert lines[0].lstrip().upper().startswith("IF "), f"{path.name}: unguarded batch\n{batch}"
