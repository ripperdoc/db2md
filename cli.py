import typer
import os
import json
from db2md.batch import Batch, Column
from db2md.main import doc_generator, job_doc_to_markdown

app = typer.Typer()

@app.command()
def convert(
    file: str, 
    out_folder: str, 
    filter: str = "", 
    log_level: str = "WARN", 
    extra_metadata: str = "", 
    dry_run: bool = False, 
    no_metadata: bool = False):

    # No need, let user pick their exact folder instead
    out_folder = os.path.abspath(out_folder)
    # out_folder = os.path.join(output_folder, os.path.splitext(os.path.basename(wiki_xml_file))[0])
    # os.makedirs(out_folder, exist_ok=True)
    columns = [Column(header="Title", import_key="title"), Column(header="Path", result_key="path")]
    extra_metadata = json.loads(extra_metadata) if extra_metadata else {}
    b = Batch(
        f"Database to Markdown: {file}",
        log_level=log_level,
        dry_run=dry_run,
        table_columns=columns,
        no_metadata=no_metadata,
        extra_metadata=extra_metadata,
        filter=filter,
        all_pages={},
        out_folder=out_folder,
    )
    b.process(doc_generator(file), job_doc_to_markdown)
    print(b.summary_str())    


if __name__ == "__main__":
    app()