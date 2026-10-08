"""Help > How to use: the everyday flow, what the row states mean, and where things go."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout

FLOW = """
<h3>Everyday use</h3>
<ol>
<li>Put new books in your <b>Incoming</b> folders (audiobooks / books).</li>
<li><b>Scan</b> (F5) reads them. Nothing is changed yet.</li>
<li><b>Look up online</b> (Ctrl+L) asks Audible, AniList, Storytel and the others to confirm title, author and
series. Sure matches make a book <b>Ready</b>.</li>
<li>Ready books are ticked. Look at the <b>Check</b> ones (see the States tab), fix or tick them.</li>
<li><b>Sort ticked books</b> (Ctrl+Enter). The toolbar chooses <b>Copy</b> (originals stay in Incoming) or
<b>Move</b> (originals leave Incoming; their old cover.jpg / metadata.json go to the Done folder).</li>
<li>Check the result in your Audiobookshelf test libraries, then move the books into your real libraries
yourself.</li>
</ol>
<p>Every sort and every tool is one <b>run</b> that can be undone: <b>Edit &gt; History</b>. Nothing is ever
overwritten, and the protected (real) library folders are never touched.</p>
"""

STATES = """
<h3>What the states mean</h3>
<table cellpadding="5" border="1" cellspacing="0">
<tr><th>State</th><th>Meaning</th><th>Ticked?</th><th>What to do</th></tr>
<tr><td><b>Ready</b></td><td>Title, author and series are confirmed (tags, metadata file or an online match).</td>
<td>Yes</td><td>Sort it.</td></tr>
<tr><td><b>Check</b></td><td>Probably right, but something was only <i>guessed</i> from file or folder names, or an
online source disagrees. The reason is in the notes on the right.</td><td>No</td>
<td>Read the note and the target path. Right: tick it. Wrong: fix the fields and <b>Save changes</b>, or
<b>Search</b> and <b>Use selected match</b>.</td></tr>
<tr><td><b>Review</b></td><td>No author could be found.</td><td>Yes</td>
<td>Goes to the Review folder with a .reason.txt - or add the author first.</td></tr>
<tr><td><b>Problem</b></td><td>Can't be sorted: a duplicate, the target already exists, a protected folder...</td>
<td>No</td><td>Read the note. Duplicates: <b>Choose which copy to keep</b>.</td></tr>
<tr><td><b>Unknown</b></td><td>Not even a title.</td><td>No</td><td>Fill in the fields, or skip it.</td></tr>
<tr><td><b>Skipped</b></td><td>You left it out (Delete key).</td><td>No</td><td>Delete again to bring it back.</td></tr>
<tr><td><b>Sorted</b></td><td>Already done - only listed under <b>Sorted</b> in the sidebar, not under All.</td><td>No</td><td>-</td></tr>
</table>
<p>The right panel shows where each field came from: <i>tags</i>, <i>sidecar</i> (metadata.json / .opf),
<i>filename</i>, <i>folder</i>, or an online source like <i>audible</i> or <i>anilist</i>. Fields from
<i>filename</i> / <i>folder</i> are guesses - that's what makes a book Check.</p>
<h3>The cover</h3>
<p>The picture in the right panel is the cover sorting will write. Below it: its size and where it comes from
(<i>inside the book</i>, <i>cover file next to the book</i>, <i>Audible</i>, <i>MangaDex (English edition)</i>,
<i>bigger online cover</i>...). Under 600 px it says <b>low-res</b> in orange - the online lookup already tried
to find a bigger one. The order: a cover that came with the book &gt; an English MangaDex volume cover &gt; the
book's own cover &gt; anything online. A thumbnail cover.jpg never beats a big cover inside the book, a
Japanese-only cover never beats the English edition's own, and Audible's 2400 px cover beats small embedded
audiobook art.</p>
"""

FOLDERS = """
<h3>Where things go</h3>
<table cellpadding="5" border="1" cellspacing="0">
<tr><td><b>Sorted audiobooks</b></td><td>Audiobooks \\ Author \\ Series \\ N - Title, and Light Novels \\ ... for
audiobooks of light novels</td></tr>
<tr><td><b>Sorted books</b></td><td>Manga \\, Light Novels \\, EBooks \\, Comics \\ - each Author \\ Series \\ N - Title</td></tr>
<tr><td><b>Review</b></td><td>Books without an author</td></tr>
<tr><td><b>Duplicates</b></td><td>Extra copies (Tools &gt; Move duplicates) - the best copy stays</td></tr>
<tr><td><b>Done</b></td><td>Originals after sorting (Move mode, or Tools &gt; Clear sorted originals), one folder
per day; can empty itself to the Recycle Bin (Settings &gt; Sorting). Switch off <b>Keep old copies in Done</b>
to not keep them at all: they go to the Recycle Bin, or are deleted for good on a drive without one (the sorted book
is always complete first; undo then keeps the sorted copy). The status bar shows how big Done is.</td></tr>
</table>
<p><b>Audiobookshelf tip:</b> point a library at the kind folders (e.g. <i>...\\AudioBooks Sorted\\Audiobooks</i>),
not at the sorted folder itself - otherwise it reads "Audiobooks" as the author.</p>
<h3>Handy tools</h3>
<ul>
<li><b>Tools &gt; Edit a sorted book</b> (Ctrl+E): fix author / title / series of a book that is already sorted;
the folder moves to match.</li>
<li><b>Tools &gt; Update sorted manga</b>: official English covers and volume titles (MangaDex, Fandom wikis).</li>
<li><b>Tools &gt; Improve covers of sorted books</b>: finds books with no cover or a low-res one (under 600 px)
and offers a bigger one - from inside the book, Audible, or the same edition by ISBN. Audiobook covers also go
inside the .m4b. Old covers are kept (undo from History).</li>
<li><b>MP3 &rarr; M4B</b> (switch under Sort): audiobooks made of several files (mp3 ...) become one
chaptered .m4b while sorting. Books that already are one .m4b are left alone.</li>
<li><b>M4B &rarr; M4B</b> (switch under Sort): books that already are one .m4b get Audible's chapter names.
<b>Settings &gt; Sorting &gt; M4B &rarr; M4B</b> picks how: <i>Keep audio</i> (fast, the sound is copied as it
is) or <i>Re-encode AAC</i> (new AAC audio at the same bitrate - slow). In Move mode the original goes to the
Done folder.</li>
<li><b>Play test</b> (Settings &gt; Sorting): every audio file the app makes or changes is played through with ffmpeg
before the original leaves Incoming - <i>Full</i> (the whole file, about 20 s per book) or <i>Quick</i> (start,
middle, end). A new file that fails is removed and the original is sorted as it is; the result says why.</li>
<li><b>Edit &gt; Mark selected as already sorted</b> (Ctrl+M): hide books your library already has.</li>
</ul>
<h3>Portable</h3>
<p>Put a folder named <b>BookSorter-data</b> next to BookSorter.exe and everything the app saves - settings, history (undo), cache, ffmpeg and the window layout - stays in there, not in your user profile or the registry. Copy the two together to a USB stick or another PC. Delete the folder to go back to normal mode.</p>
<ul>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How to use Book Sorter")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for title, html in (("Everyday use", FLOW), ("States", STATES), ("Folders && tools", FOLDERS)):
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setHtml(html)
            tabs.addTab(view, title)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
