from db2md.batch import Batch, Job, JobSuccess, LogLevel
from db2md.main import doc_generator, job_doc_to_markdown
from pathlib import Path
import pytest


# Removed this test code as ElementTree no longer supports it
# &lsaquo; &rsaquo; &laquo; &raquo;
# &lsquo; &rsquo; &ldquo; &rdquo;
# &apos; &quot;


@pytest.fixture
def docs():
    docs = list(doc_generator("tests/testdata/test_mediawiki.xml"))
    assert len(docs) == 4  # Contains 4 test articles
    return docs


def test_normal_conversion(docs, tmp_path):
    test_batch = Batch(
        "Test",
        dry_run=False,
        out_folder=tmp_path,
        all_pages={},
        extra_metadata={"author": "kalle"},
    )

    assert docs[0]["title"] == "Normal"
    assert docs[0]["created_at"] == "2011-03-13T18:42:38Z"
    assert docs[0]["author"] == "Ymir"  # Replaced in output with "kalle" as per extra_metadata

    job = Job(0, batch=test_batch)
    job_doc_to_markdown(job, docs[0])
    print("\n".join(map(str, job.log)))
    assert job.success is JobSuccess.WARN  # Should have gottnen som WARN messages from fixes

    correct = Path("tests/testdata/Normal.correct").read_text()
    generated = Path(f"{tmp_path}/Normal.md").read_text()
    assert correct == generated


def test_tricky_conversion(docs, tmp_path):
    test_batch = Batch("Test", dry_run=False, out_folder=tmp_path, all_pages={})

    assert docs[1]["title"] == "'Tricky: Ϡ"
    assert docs[1]["created_at"] == ""
    assert docs[1]["author"] == "Ymir"

    job = Job(1, batch=test_batch)
    job_doc_to_markdown(job, docs[1])
    print("\n".join(map(str, job.log)))
    assert job.success is JobSuccess.WARN  # Should have gottnen som WARN messages from fixes

    correct = Path("tests/testdata/'Tricky Ϡ.correct").read_text()
    generated = Path(f"{tmp_path}/'Tricky Ϡ.md").read_text()
    assert correct == generated

    job2 = Job(2, batch=test_batch)
    job_doc_to_markdown(job2, docs[1])
    # Should fail because we try to import same again, and don't want to overwrite
    assert job2.success is JobSuccess.FAIL


def test_wrong_namespace(docs, tmp_path):
    test_batch = Batch("Test", dry_run=False, out_folder=tmp_path, all_pages={}, log_level=LogLevel.DEBUG)

    assert docs[2]["title"] == "Mall:Test"
    assert docs[2]["created_at"] == "2012-09-25T15:56:33Z"
    assert docs[2]["author"] == ""

    job = Job(1, is_dry_run=True, batch=test_batch)
    job_doc_to_markdown(job, docs[2])
    assert job.result is None
    assert job.success is JobSuccess.SKIP  # Should skip as it's a Mall: namespace


def test_empty_title(docs, tmp_path):
    test_batch = Batch("Test", dry_run=False, out_folder=tmp_path, all_pages={}, log_level=LogLevel.DEBUG)

    assert docs[3]["title"] == ""
    assert docs[3]["created_at"] == "2011-06-08T05:17:04Z"
    assert docs[3]["author"] == ""

    job = Job(1, is_dry_run=True, batch=test_batch)
    with pytest.raises(AssertionError):  # Should assert on empty title
        job_doc_to_markdown(job, docs[3])
    assert job.success is JobSuccess.INCOMPLETE  # As we excited in exception, it is incomplete


# test filtering

# test redirect

# test that two identical titles with different case won't overwrite each other
