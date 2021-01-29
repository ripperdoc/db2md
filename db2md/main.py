import io
import json
import os
import re
import sqlite3
import time
import datetime
from codecs import decode, encode
from pathlib import Path
from typing import Dict, Mapping, NamedTuple, Optional, Pattern, Union
from xml.etree import ElementTree as ET

from dateutil.parser import parse
import panflute as pf
import yaml
from panflute.elements import Doc

from .batch import Job, JobSuccess, LogLevel
from .unicode_slugify import SLUG_ID, slugify

"""
Still TODO

MEDIAWIKI
Drängopedia uses *, **, *** as footnotes, which turn into ugly formatting. A better way would be a real footnote plugin with anchor link, or replacing them with ¹, ² etc 
Wikilinks#WithAnchor makes the # as an espaced special char. Should fix in link filter to put to the URI side
space inside bold or italics inlines, should be outside. If space used inside '' and ''' Pandoc seems to fail to convert it to Markdown.
Remove double space inside text
Accidental dash in beginning of line, making a list but intended as "tankestreck"
Fix incorrect title characters: MediaWiki allows these [%!\"$&'()*,\-.\/0-9:;=?@A-Z\\^_`a-z~\x80-\xFF+], and disallows #<>[]|{}
Remove image captions that are just the filename repeated
Merge subsequent lines with `abc` to a ```abc``` block

WORDPRESS

- Many duplicates due to now permalink awareness?
- Many unusual whitespace characters

Nestes image link not working:
[<img src="http://kampanj.ripperdoc.net/wp-content/uploads/ALIENSATOZ-33.jpg" title="ALIENSATOZ-33" class="alignright size-full wp-image-758" />]

Wrong snippets (search)
2d[ ]{.Apple-style-span style="color: #333333; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 15px; line-height: 24px;"} 

  [brev]: %5Baurl%20t95%5D

&lt;ngn&gt; istället för \\< \\>

Messy asterisks
**Lawube:*** Minns honom som en mycket äldre broder.* **Annan relation:** *Deliolith* **Annan relation:** *Akoia* **Personlighet och motiv** *Beskriv (allt behöver fyllas i!)*

-   ***Beteende**:** **Formell, ibland arrogant. Självsäker.*

Remaining Wordpress shortcodes

\[pe2-gallery album="aHR0cDovL3BpY2FzYXdlYi5nb29nbGUuY29tL2RhdGEvZmVlZC9iYXNlL3VzZXIvcmlwcGVyZG9jL2FsYnVtaWQvNTQ4NDg5NDE4NTY1OTkwOTM3Nz9hbHQ9cnNzJmFtcDtobD1lbl9VUyZraW5kPXBob3Rv"\]

Missing paragraph breaks from texts like Kapitel II - Asköken.md

Table not converted in Chudo.md

**Utrustning        **Lägenhet i centrala Tampa. 4000 €

::: {#bodyContent}
:::

-   **Beteende**:** **Nyfiken

OTHER FORMATS

Add Joomla parsing

"""

# PATTERNS

# Mediawiki legal characters:
#  $wgLegalTitleChars = %!\"$&'()*,\\-.\\/0-9:;=?@A-Z\\\\^_`a-z~\\x80-\\xFF+
#  Filenames have to be valid titles as above but also has additional illegal characters:
#  $wgIllegalFileChars = :\\/\\\\ . Illegal characters are replaced with -

category_pattern = re.compile(r"category|kategori", flags=re.IGNORECASE)
wiki_redirect_pattern = re.compile(r"^#(REDIRECT|OMDIRIGERING) ", flags=re.IGNORECASE)
# link_text_invalid_chars = re.compile(r'[#<>[\]\|{}`\\]+')
# link_text_invalid_chars = re.compile("[^%!\"$&'()*,\\-.\\/0-9:;=?@A-Z\\\\^_`a-z~\\x80-\\xFF+]")
# uri_invalid_chars = re.compile("[]")

ignore_ns = (
    "Special|Talk|Diskussion|User|Användare|User_talk|Användardiskussion|File_talk|Fildiskussion|"
    "MediaWiki|MediaWiki_talk|MediaWiki-diskussion|Template|Mall|Template_talk|Malldiskussion|Help|"
    "Hjälp|Help_talk|Hjälpdiskussion|Category_talk|Kategoridiskussion|Bilddiskussion"
)
media_ns = "Media|File|Image|Fil|Bild"
category_ns = "Category|Kategori"
# Note that links may look like [](Category:_A_Thing) and should be [](A_Thing) after dropping NS
# (?!/): means match : if not before a /, as in http://
ns_divider = "[_\\s]*:(?!/)[_\\s]*"

all_ns_pattern = re.compile(
    f"[: ]?((?P<ns>{ignore_ns}|{media_ns}|{category_ns}){ns_divider})?(?P<rest>.*)",
    flags=re.DOTALL | re.IGNORECASE,
)
ignore_ns_pattern = re.compile(f"[: ]?({ignore_ns})", flags=re.DOTALL | re.IGNORECASE)


class RegexFix(NamedTuple):
    pattern: Pattern
    repl: Union[str, None]
    comment: str
    log_level: Optional[LogLevel] = LogLevel.DEBUG


# HTML FIXES

html_fixes = {}


# MEDIAWIKI FIXES

mediawiki_fixes = {
    "behavior_switches_pattern": RegexFix(
        re.compile(r"__\w+__"),
        "",
        "A Mediawiki magic word like __TOC__ and __NOTOC__ https://www.mediawiki.org/wiki/Help:Magic_words",
    ),
    "fix_broken_tables": RegexFix(
        re.compile(r" = "),
        "=",
        "MW tables may have space around = when setting attributes, but Pandoc before 2.11 won't have it",
    ),
    "normalize_image_links": RegexFix(
        re.compile(r"\[\[(File|Image|Fil|Bild):", flags=re.IGNORECASE),
        "[[Image:",
        "Pandoc doesn't recognize localized or incorrectly cased namespaces as image links, e.g. `[[Fil:`, `[[image:`, `[[file:`.",
    ),
    "asterisk_horizontal_line": RegexFix(
        re.compile(r"^\*\*\*+", flags=re.MULTILINE),
        "----",
        "Some MW source may contain ****** as a horizontal line, but replaced to ---- to work in Pandoc",
    ),
    "implied_heading": RegexFix(
        re.compile(r"^''' *(.+?) *'''(<br>|</br>| )*$", flags=re.MULTILINE),
        "==== \\1 ====\n",  # Low level heading (H4) assuming we get re-balanced by action_arrange_headings()
        "Bold words on single row are taken as implied H2 heading",
    ),
    "implied_list": RegexFix(
        re.compile(r"^'''(.*)(<br>|</br>)", flags=re.MULTILINE),
        "* '''\\1",
        "Lines starting with bold and ending with <br> are treated as a list item.",
    ),
    "strikethrough_pattern": RegexFix(
        re.compile(r"<s>(.+?)</s>"),
        "<del>\\1</del>",
        "Pandoc only recognizes <del> as strike-through, so change <s> to <del>",
    )
    # Templates and MW variables are automatically removed by Pandoc
    # "variables_pattern": (
    #     r"{{[^}]+}}",
    #     "A Mediawiki variable or template, cannot be translated to Markdown but consider removing",
    # ),
}

# MARKDOWN FIXES

markdown_fixes = {
    "empty_image_link": RegexFix(
        re.compile(r"!\[\]\[\d\]"), None, "Empty image reference links found, should be captioned", LogLevel.INFO
    ),
    "html_comment_pattern": RegexFix(
        re.compile(r"<!--.+?-->"),
        None,
        "HTML comment found. Pandoc may add to avoid lists merging unintentionally. Clean manually.",
        LogLevel.WARN,
    ),
    "escaped_chars": RegexFix(
        re.compile(r"\\[-\"'*_#+.(){}[\]]"),
        None,
        "Markdown special characters escaped, consider changing the text to user other character",
        LogLevel.WARN,
    ),
    "html_tag_pattern": RegexFix(
        re.compile(r"<(?!(!|http)).+?>"), None, "Possibly unintentional HTML tag", LogLevel.WARN
    ),
    "unconverted_mediawiki_formatting": RegexFix(
        re.compile(r"^=+"), None, "Looks like a Mediawiki heading is still in the text", LogLevel.WARN
    ),
    "use_list_instead": RegexFix(
        re.compile(r"\\\n\S+\\\n"), None, "Multple lines using forced line break, maybe make a list?", LogLevel.WARN
    ),
    "link_as_header": RegexFix(
        re.compile(r"\[.+?\]\\\n"),
        None,
        "Instead of link as header on single line, maybe add together as [Link]: text",
        LogLevel.WARN,
    ),
    "space_on_line_endings": RegexFix(
        re.compile(r"[^\S\r\n]+$", re.MULTILINE), "", "Remove whitespaces at end of lines"
    ),  # may break intentional markdown line breaks using double space
    "empty_ref_link": RegexFix(
        re.compile(r"\[(.+?)\]\[\]"),
        "[\\1]",
        "Pandoc can't write Commonmark with shortcut reference links ([link] instead of [link][], so we fix: ",
    ),
    "inline_code_intended_as_block": RegexFix(
        re.compile(r"(`[^`]+`\n)+"),
        None,
        "Complete lines marked as inline code, should probably be code block ``` ```",
        LogLevel.WARN,
    ),
    "multiple_empty_rows": RegexFix(
        re.compile(r"\n\n\n+"),
        "\n\n",
        "Reduce 3 or more newlines in a row to just 2",
    ),
    "remaining_wikicode": RegexFix(
        re.compile(r"['=]{2,}"),
        None,
        "Some Mediawiki formatting is still left, may be error in Pandoc, need to fix manually",
        LogLevel.WARN,
    ),
    "unusual_whitespace": RegexFix(
        re.compile(r".?[^\S\r\n ]+"),  # ^\S = Not not-whitespace, http://jkorpela.fi/chars/spaces.html
        None,
        "Unusual whitespace character detected (not regular space or line break)",
        LogLevel.WARN,
    )
    # unescape = re.compile(r"\\([^\`*_{}\[\]\(\)>#+-.!])")  # Replace with group
}

def parse_datetime(dt_string: str) -> datetime.datetime:
    # Parses any type of date string, ISO or other
    try:
        return parse(dt_string) if isinstance(dt_string, str) else None
    except ValueError:
        return None

def simple_truncate(s, n=10):
    s = s.strip()
    return s[:10] + (s[10:] and "…")


def apply_regex_fixes(s: str, fixes: Mapping[str, RegexFix], job: Job = None):
    for k, v in fixes.items():
        if v.repl is not None:
            s, count = v.pattern.subn(v.repl, s)
            if job and count > 0 and v.log_level is not None:
                job.log_any(f"Replaced {k} {count} times", v.log_level)
        elif job and v.log_level is not None:
            # Truncate to 10 chars plus ellipsis
            # TODO maybe print each find in special background color on terminal to easily see start and end?
            matches = ", ".join([f"{simple_truncate(m[0])}" for m in v.pattern.finditer(s)])
            if matches:
                job.log_any(f"{v.comment}, at {matches}", v.log_level)
    return s


def make_sqlite_safe(script: str) -> str:
    """A quick and dirty way to convert from e.g. MySQL to SQLite.
    artly based on https://gist.github.com/grfiv/b79ace3656113bcfbd9b7c7da8e9ae8d ,
    and https://stackoverflow.com/questions/41026122/creating-sql-thats-compatible-with-both-sqlite-and-mysql-with-auto-increment
    Args:
        script (str): [description]

    Returns:
        str: [description]
    """
    script = re.sub(r"(int\(\d+\)) unsigned", "\\1", script, flags=re.MULTILINE)
    script = re.sub(r"ENGINE=.*;$", ";", script, flags=re.MULTILINE)
    script = re.sub(r" ?AUTO_INCREMENT ?", "", script, flags=re.MULTILINE)
    script = re.sub(r"^CREATE DATABASE.*;$", "", script, flags=re.MULTILINE)
    script = re.sub(r"^USE .*;$", "", script, flags=re.MULTILINE)
    script = re.sub(r",?\s*(FULLTEXT|PRIMARY|UNIQUE)?\s*KEY .*$", "", script, flags=re.MULTILINE)
    script = re.sub(r"^(UN)?LOCK.*$", "", script, flags=re.MULTILINE)
    script = re.sub(r"ON UPDATE.*(,)?$", "\\1", script, flags=re.MULTILINE)
    script = re.sub(r"enum\(.*,$", "blob,", script, flags=re.MULTILINE)
    script = re.sub(r"\\'", "''", script, flags=re.MULTILINE)  # Sqlite escapes ' as '', not \'
    return script


def clean_escaping(s):
    return decode(encode(s, "latin-1", "backslashreplace"), "unicode-escape")


def doc_generator(db_file):
    if db_file.endswith(".xml"):
        tree = ET.parse(db_file)
        root = tree.getroot()
        ns = "{http://www.mediawiki.org/xml/export-0.3/}"
        for page in root.findall(f".//{ns}page"):
            yield {
                "title": page.findtext(f"{ns}title", ""),
                "created_at": page.findtext(f".//{ns}timestamp", ""),
                "author": page.findtext(f".//{ns}username", ""),
                "text/x-wiki": page.findtext(f".//{ns}text", ""),
            }
    elif db_file.endswith(".sql"):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        script = Path(db_file).read_text(encoding="utf-8", errors="ignore")
        script = make_sqlite_safe(script)
        cur = con.cursor()
        cur.executescript(script)
        if "CREATE TABLE `page`" and "CREATE TABLE `revision`" in script:
            # https://www.mediawiki.org/wiki/Manual:Database_layout
            for row in cur.execute(
                """
                SELECT page_title,page_namespace,rev_timestamp,user_name,old_text FROM
                    page
                        INNER JOIN revision ON page.page_latest = revision.rev_id
                            INNER JOIN user ON revision.rev_user = user.user_id
                                INNER JOIN text ON revision.rev_text_id = text.old_id
                """
            ):

                if row["page_namespace"] != 0:
                    # Skip non-main pages, e.g. talk, user, file, etc https://gerrit.wikimedia.org/g/mediawiki/core/+/HEAD/includes/Defines.php
                    continue
                data = {
                    "title": clean_escaping(row["page_title"].replace("_", " ")),
                    "created_at": str(row["rev_timestamp"]),
                    # Strings are escaped in text fields in SQL, check
                    # https://stackoverflow.com/questions/1885181/how-to-un-escape-a-backslash-escaped-string
                    "text/x-wiki": clean_escaping(row["old_text"]),
                    "author": row["user_name"],  # Also get email
                }
                yield data

        elif "CREATE TABLE `wp_posts`" in script and "CREATE TABLE `wp_users`" in script:
            for row in cur.execute(
                """
                SELECT * FROM
                    wp_posts
                        INNER JOIN wp_users ON wp_posts.post_author = wp_users.ID
                """
            ):
                data = {
                    "title": clean_escaping(row["post_title"]),
                    "created_at": str(row["post_date_gmt"]),
                    "updated_at": str(row["post_modified_gmt"]),
                    # Strings are escaped in text fields in SQL, check
                    # https://stackoverflow.com/questions/1885181/how-to-un-escape-a-backslash-escaped-string
                    "text/html": clean_escaping(row["post_content"]),
                    "author": row["display_name"],  # Also get email
                }
                yield data
        elif "CREATE TABLE `mos_content`" in script:
            for row in cur.execute(
                """
                SELECT * FROM
                    mos_content
                        INNER JOIN wp_users ON wp_posts.post_author = wp_users.ID
                """
            ):
                data = {
                    "title": clean_escaping(row["title"]),
                    "created_at": str(row["created"]),
                    "updated_at": str(row["modified"]),
                    "text/html": clean_escaping(row["fulltext"]),
                }


def action_scan_headings(elem, doc, job, context):
    """Will just scan all headings and note their level in a dict, for later use.

    Args:
        elem (Element): current element in the Panflute syntax tree
        doc (Doc): representing full document
        job (Job): a job object holding the batch job context
    """
    if isinstance(elem, pf.Header):
        context["headings"][elem.level] = 0


def action_balance_headings(elem, doc, job, context):
    """Balances headings so they always start with H1 and increase step by step.

    Args:
        elem (Element): current element in the Panflute syntax tree
        doc (Doc): representing full document
        job (Job): a job object holding the batch job context
    """
    if isinstance(elem, pf.Header):
        if elem.level in context["headings"]:
            elem.level = context["headings"][elem.level]
        if elem.content and isinstance(elem.content[0], pf.Strong):
            strong_contents = elem.content[0].content
            del elem.content[0]
            for i in range(len(strong_contents) - 1, -1, -1):
                elem.content.insert(0, strong_contents[i])


def action_clean_link(elem, doc, job, context):
    """Cleans some nodes in the syntax tree:
    - removing "wikilink" title from links
    - remove "fig:" prefix from title in links
    - remove linebreaks if first in paragraph

    Args:
        elem (Element): current element in the Panflute syntax tree
        doc (Doc): representing full document
        job (Job): a job object holding the batch job context

    Returns:
        [type]: [description]
    """
    if isinstance(elem, pf.Link):
        # Read at end for explanation of why wikilink https://github.com/jgm/pandoc/issues/5414
        if elem.title == "wikilink":
            elem.title = ""
        if len(elem.content) == 0:
            s = elem.title or elem.url
            elem.content = [pf.Str(s)]
    elif isinstance(elem, pf.Image):
        if elem.title.startswith("fig:"):
            elem.title = elem.title[4:]
        elem.attributes.clear()
    elif isinstance(elem, pf.LineBreak):
        if elem.index == 0:  # No need for a LineBreak at beginning of a paragraph
            job.debug("Removed hard line break at start of paragraph")
            return []


def action_extract_namespace(elem, doc, job, context):
    """Many wikilinks include a namespace, take note of the namespace semantic meaning and then remove it from the link.

    Args:
        elem (Element): current element in the Panflute syntax tree
        doc (Doc): representing full document
        job (Job): a job object holding the batch job context

    Returns:
        [type]: [description]
    """
    if isinstance(elem, pf.Link) or isinstance(elem, pf.Image):
        url_match = all_ns_pattern.match(elem.url)
        # assert text_match is not None, f"all_ns_pattern ({all_ns_pattern.pattern}) didn't match '{matchobj.group(1)}'"
        assert url_match is not None, f"all_ns_pattern ({all_ns_pattern.pattern}) didn't match '{elem.url}'"
        assert (
            url_match.group("rest") is not None
        ), f"all_ns_pattern ({all_ns_pattern.pattern}) didn't get rest group '{elem.url}'"
        elem.url = url_match.group("rest").strip()
        assert elem.url
        ns = url_match.group("ns")
        if context["is_redirect"]:
            context["raw_metadata"].setdefault("alias_for", set()).add(
                slugify(elem.url.replace("_", " "), lower=False, spaces=True)
            )
            return elem
            # return []  # Remove the link
        elif isinstance(elem, pf.Image):
            context["raw_metadata"].setdefault("image", set()).add(elem.url)
            return elem
        elif ns:
            # leading : in namespace means not intended as category in wikilinks
            if category_pattern.match(ns):
                context["raw_metadata"].setdefault("category", set()).add(elem.url.replace("_", " "))
                return []  # Category links remove themselves
            elif ignore_ns_pattern.match(ns):
                return pf.Strikeout(*elem.content)
            else:
                job.warn(f"Forcing unknown namespace {ns} from {url_match.group(0)} to be a mention")
        if not elem.url.startswith("http"):  # Don't count regular URLs as mentions
            # context["raw_metadata"].setdefault("mention", set()).add(elem.url.replace("_", " "))  # Skip mentions as reference links come at end anyway
            elem.url = slugify(elem.url, lower=False, spaces=True)


def prepare_balanced_headings(h):
    offset = 0
    if 1 in h:  # Ensure h1 is converted to h2 and everything else is pushed "down"
        h[1] = 2
        offset += 1
    for i in range(2, 7):
        if i in h:
            h[i] = min(i + offset, 6)
        else:
            offset -= 1
    return h


def clean_metadata(metadata: Dict):
    # Temporarily skip the RawInline as we don't go through Pandoc to write yaml
    for k, v in metadata.items():
        if isinstance(v, list) or isinstance(v, set):
            if v:
                metadata[k] = sorted(v)
                # metadata[k] = list(map(lambda x: pf.RawInline(x), sorted(v)))
            else:
                del metadata[k]
        elif isinstance(v, dict):
            metadata[k] = clean_metadata(v)
        else:
            pass
            # metadata[k] = pf.RawInline(v)
    return metadata


def job_doc_to_markdown(job: Job, data):
    assert "title" in data
    title = data["title"]
    assert len(title) > 0, "Title cannot be empty"
    text: str = ""
    text_type: str = ""
    if text := data.get("text/x-wiki", ""):
        text_type = "text/x-wiki"
    elif text := data.get("text/html", ""):
        text_type = "text/html"
    assert text
    assert text_type in ["text/x-wiki", "text/html"]

    file_birthtime = None
    assert job.context.get("out_folder", ""), "out_folder path not in context"
    if not isinstance(job.context.get("all_pages", None), dict):
        job.context["all_pages"] = {}
    written_docs = job.context["all_pages"]

    # id is lowercased so we can check case insensitively
    id = slugify(title, ok=SLUG_ID, spaces=True)
    assert id, "ID is empty"
    job.id = id

    # File path retains mixed case vs ID, as it looks better and fits Github Pages/Wiki if uploaded
    file_path = os.path.join(job.context["out_folder"], slugify(title, ok=SLUG_ID, lower=False, spaces=True) + ".md")

    if (filter := job.context.get("filter", "")) and filter.lower() not in id:
        # print(f"filter={job.context['filter']}, id={id}, in it={job.context['filter'] in id}")
        return job.complete(JobSuccess.SKIP)

    match = all_ns_pattern.match(title)
    if match and match.group("ns"):
        return job.warn("Skipping doc as title includes a Mediawiki namespace").complete(JobSuccess.SKIP)

    new_text, is_redirect = wiki_redirect_pattern.subn("Alias for ", text) if text_type == "text/x-wiki" else ("", 0)
    if is_redirect:
        text = new_text

    # If there is a doc with same id already we need to decide if we overwrite the old or fail the new
    # We deem it safe to overwrite an existing doc if it's just a redirect, as they have the same ID (but would lose variations on the title)
    # Note that we always overwrite _existing files_, this just checks if we already created the file earlier in the same batch
    # Logic table
    # old_redir	new_redir	action
    # 1	        0	        overwrite old doc
    # 1	        1	        overwrite old doc
    # 0	        0	        fail new doc
    # 0	        1	        fail new doc
    # TODO another logic here could be to add unique suffix to id to save both new and old, but then it would break links pointing to new
    # TODO if new is redir but not old, we could also add that manually as a variant name to the old YAML, if we allow ourselves to manyally correct the file
    if written_docs.get(id, (0, 1))[1] == 0:  # Old doc is not redirect
        return job.error(
            f"Forced to skip this doc '{title}' as it might overwrite already processed doc with '{os.path.join(job.context['out_folder'], written_docs[id][0])}.md'"
        ).complete(JobSuccess.FAIL)
    elif id in written_docs:
        job.warn("Overwrote older redirect doc with same id")

    written_docs[id] = (title, is_redirect)

    # Apply fixes on Mediawiki input
    doc = Doc()
    if text_type == "text/x-wiki":
        text = apply_regex_fixes(text, mediawiki_fixes)
        doc: Doc = pf.convert_text(text, input_format="mediawiki", output_format="panflute", standalone=True)  # type: ignore

    elif text_type == "text/html":
        text = apply_regex_fixes(text, html_fixes)
        doc: Doc = pf.convert_text(text, input_format="html", output_format="panflute", standalone=True)  # type: ignore

    context = {"raw_metadata": {}, "headings": {}, "is_redirect": is_redirect}
    pf.run_filters([action_scan_headings], doc=doc, job=job, context=context)
    prepare_balanced_headings(context["headings"])
    actions = [action_balance_headings, action_clean_link]
    if text_type == "text/x-wiki":
        actions += [action_extract_namespace]
    elif text_type == "text/html":
        pass

    pf.run_filters(actions, doc=doc, job=job, context=context)

    if not job.batch.no_metadata:
        context["raw_metadata"]["id"] = id
        context["raw_metadata"]["title"] = title
        if data.get("created_at", False):
            dt = parse_datetime(data["created_at"])
            context["raw_metadata"]["created_at"] = dt.isoformat()
            file_birthtime = dt
        if data.get("updated_at", False):
            dt = parse_datetime(data["updated_at"])
            context["raw_metadata"]["updated_at"] = dt.isoformat()
        if data.get("author", False):
            context["raw_metadata"]["author"] = data["author"]
        if job.context.get("extra_metadata", None):
            # Override with provided metadata
            context["raw_metadata"].update(job.context.get("extra_metadata", None))
        # Sort all metadata and wrap every item in RawInline to avoid markdown escaping. See issue https://github.com/jgm/pandoc/issues/2139
        doc.metadata = clean_metadata(context["raw_metadata"])

    # Normal commonmark has none of below, but commonmark_x has those with +
    # pandoc --list-extensions=commonmark_x
    # -ascii_identifiers
    # +auto_identifiers
    # -autolink_bare_uris
    # +bracketed_spans
    # +definition_lists
    # -east_asian_line_breaks
    # +emoji
    # +fancy_lists
    # +fenced_code_attributes
    # +fenced_divs
    # +footnotes
    # -gfm_auto_identifiers
    # -hard_line_breaks
    # -implicit_figures
    # +implicit_header_references
    # +pipe_tables
    # +raw_attribute
    # +raw_html
    # +raw_tex
    # +smart
    # +strikeout
    # +subscript
    # +superscript
    # +task_lists
    # +tex_math_dollars
    # +attributes

    output_format = "".join(
        [
            # "markdown",  # To standard markdown
            "commonmark_x",
            # "+yaml_metadata_block",  # Not supported yet for Commonmark https://github.com/jgm/pandoc/issues/6629
            # "-header_attributes",  # Don't write {#attribute} after headings
            # "-simple_tables",  # Don't write simple table format
            # "-link_attributes",  # Don't add attributes to links
            # "-inline_code_attributes",  # Don't add attributes to code
            # "+shortcut_reference_links", # Not supported in Pandoc yet for commonmark
            "-implicit_figures",  # Don't assume images with alt is a figure
            "-raw_attribute",  # Don't give attributes to raw content
            "-smart"
            # Smart would normally convert md straight quotes to curly unicode in HTML.
            # ' to ‘ ’,  " to “ ”, << >> to « », ... to …, -- to – (ndash), --- to — (mdash)
            # But when we write markdown, for some reason keeping smart will put backslash before
            # ... and ' in text, so that's why we turn it off
        ]
    )

    extra_args = []
    extra_args += ["--wrap=none"]
    # extra_args += ["--columns=100"]
    extra_args += ["--reference-links"]

    mdtext: str = pf.convert_text(
        doc, input_format="panflute", output_format=output_format, standalone=True, extra_args=extra_args
    )  # type: ignore
    mdtext = apply_regex_fixes(mdtext, markdown_fixes, job=job)

    json_str = ""
    if job.is_debug:
        with io.StringIO() as fs:
            pf.dump(doc, fs)
            json_str = json.dumps(json.loads(fs.getvalue()), indent=2)  # Prettifies JSON

    if job.is_dry_run:
        if job.is_bugreport:
            print("BUGREPORT:\n------------")
            print(f"pandoc -f mediawiki -t {output_format} {' '.join(extra_args)} <<EOF\n{text}\nEOF")
            print(json_str)
            print("------------")
        return job.complete(result={"text": mdtext, "debug": json_str, "path": file_path})
    else:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)  # Ensure dir exists
        with open(file_path, "w") as f:
            # Manually write YAML header as not supported yet for Commonmark https://github.com/jgm/pandoc/issues/6629
            f.write("---\n")
            yaml.dump(context["raw_metadata"], f, allow_unicode=True)
            f.write("---\n")
            f.write(mdtext)
        if json_str:
            with open(file_path + ".debug.json", "w") as f:
                f.write(json_str)
        if file_birthtime:
            # Says it would set just access, modified time but also sets birthtime on MacOS!
            os.utime(file_path, (time.time(), file_birthtime.timestamp()))
        return job.complete(result={"path": file_path})
