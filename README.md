# DB2MD

DB2MD is a small library with CLI tool to convert documents, posts and text content stored in various CMS database files to a clean(ish) directory of Markdown files, suitable for editing in tools like Foam or publishing in tools like Eleventy or Jekyll. The tool is focused on pulling from the raw dump files (assuming that the original site or database may no longer be live).

## Import sources

Can currently recognize source database files such as:

- Mediawiki XML export
- Mediawiki SQL dump
- Wordpress SQL dump
- Joomla SQL dump

## Output

Outputs a folder of Markdown files. The markdown is Commonmark X compatible, with some additional Frontmatter data captured from the source.

## Internals

DB2MD is built around Pandoc for conversion, but adds additional tooling and some opinionated settings and fixes for typical issues on the source texts.

## License

TBD