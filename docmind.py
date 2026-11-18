"""In-memory parsing, provenance, and extractive retrieval."""
from dataclasses import dataclass
from pathlib import PurePath
from uuid import uuid4

import pymupdf
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 10
MAX_PAGES = 200
MAX_TEXT = 600_000
MAX_PASSAGE = 900
MAX_QUESTION = 400
MAX_HISTORY = 5
MIN_SIMILARITY = 0.12
MIN_COVERAGE = 0.65
NO_ANSWER = "I couldn’t find that in the uploaded documents."


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class Passage:
    document_id: str
    filename: str
    text: str
    page: int | None = None
    first_line: int | None = None
    last_line: int | None = None

    @property
    def location(self):
        if self.page is not None:
            return f"Page {self.page}"
        return f"Lines {self.first_line}–{self.last_line}"


@dataclass(frozen=True)
class Document:
    id: str
    filename: str
    passages: tuple[Passage, ...]


@dataclass(frozen=True)
class Answer:
    question: str
    passage: Passage | None


def citation_text(answer):
    passage = answer.passage
    if passage is None:
        raise ValueError("An unsupported answer has no citation to export.")
    return (
        f"Question: {answer.question}\n"
        f"Source: {passage.filename}\n"
        f"Location: {passage.location}\n"
        f"Document ID: {passage.document_id}\n"
        f"\nExact indexed passage\n\n{passage.text}"
    )


def location_chunks(text):
    """Yield exact consecutive text spans and 1-based source line ranges."""
    buffer = []
    length = 0
    start = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip() or length + len(line) + 1 > MAX_PASSAGE:
            if buffer:
                yield "\n".join(buffer), start, number - 1
                buffer, length = [], 0
        if not line.strip():
            continue
        if len(line) > MAX_PASSAGE:
            for offset in range(0, len(line), MAX_PASSAGE):
                piece = line[offset:offset + MAX_PASSAGE]
                if piece.strip():
                    yield piece, number, number
            continue
        if not buffer:
            start = number
        buffer.append(line)
        length += len(line) + 1
    if buffer:
        yield "\n".join(buffer), start, number


def extract_pdf(raw):
    if b"%PDF-" not in raw[:1024]:
        raise DocumentError("Invalid PDF signature. Choose a valid PDF.")
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass or pdf.is_encrypted or pdf.metadata.get("encryption"):
                raise DocumentError("Encrypted PDF. Upload an unlocked copy.")
            if pdf.is_repaired:
                raise DocumentError("Corrupt PDF. Re-export the PDF and try again.")
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise DocumentError("PDF must have between 1 and 200 pages.")
            count = 0
            for number, page in enumerate(pdf, start=1):
                text = page.get_text(sort=True)
                count += len(text)
                if count > MAX_TEXT:
                    raise DocumentError("PDF exceeds 600,000 extracted characters. Split it into smaller files.")
                yield number, text
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, DocumentError):
            raise
        raise DocumentError("PDF could not be read. Re-export the PDF and try again.") from exc


def parse_document(filename, raw):
    filename = PurePath(filename.replace("\\", "/")).name
    extension = PurePath(filename).suffix.lower()
    if extension not in {".pdf", ".txt"}:
        raise DocumentError("Choose a PDF or UTF-8 .txt file.")
    if not raw:
        raise DocumentError("File is empty. Choose a document with text.")
    if len(raw) > MAX_BYTES:
        raise DocumentError("File exceeds 8 MiB. Upload a smaller file.")
    document_id = uuid4().hex
    if extension == ".pdf":
        passages = tuple(Passage(document_id, filename, value, page=page)
                         for page, text in extract_pdf(raw) for value, _, _ in location_chunks(text))
        if not passages:
            raise DocumentError("No extractable PDF text. Scanned PDFs need a searchable text layer.")
    else:
        try:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise DocumentError("Text file must be encoded as UTF-8.") from exc
        if "\x00" in text:
            raise DocumentError("Text file contains binary data. Export it as UTF-8 text.")
        if len(text) > MAX_TEXT:
            raise DocumentError("Text file exceeds 600,000 characters. Split it into smaller files.")
        passages = tuple(Passage(document_id, filename, value, first_line=start, last_line=end)
                         for value, start, end in location_chunks(text))
        if not passages:
            raise DocumentError("Text file has no readable content.")
    return Document(document_id, filename, passages)


class SearchIndex:
    def __init__(self, documents):
        self.passages = tuple(passage for doc in documents for passage in doc.passages)
        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=80_000)
        tokenize = self.vectorizer.build_tokenizer()
        self.tokens = tuple(content_tokens(tokenize, passage.text) for passage in self.passages)
        self.matrix = (self.vectorizer.fit_transform([p.text for p in self.passages])
                       if any(self.tokens) else None)

    def retrieve(self, question):
        tokenize = self.vectorizer.build_tokenizer()
        terms = content_tokens(tokenize, question)
        if self.matrix is None or not terms:
            return None
        vector = self.vectorizer.transform([question])
        if not vector.nnz:
            return None
        scores = cosine_similarity(vector, self.matrix).ravel()
        candidates = []
        for index, terms_in_passage in enumerate(self.tokens):
            shared = len(terms & terms_in_passage)
            if (shared >= min(2, len(terms)) and shared / len(terms) >= MIN_COVERAGE
                    and scores[index] >= MIN_SIMILARITY):
                candidates.append(index)
        best = max(candidates, key=lambda index: scores[index], default=None)
        return None if best is None else self.passages[best]


def content_tokens(tokenize, text):
    return frozenset(token for token in tokenize(text.lower()) if token not in ENGLISH_STOP_WORDS)


class Workspace:
    def __init__(self):
        self.clear()

    def clear(self):
        self.documents = ()
        self.index = SearchIndex(())
        self.reset_answers()

    def reset_answers(self):
        self.answer = None
        self.history = ()

    def add(self, files):
        accepted, rejected = [], []
        for filename, raw in files:
            if len(self.documents) + len(accepted) >= MAX_FILES:
                rejected.append((filename, "Workspace limit is 10 documents. Clear it before adding more."))
                continue
            try:
                accepted.append(parse_document(filename, raw))
            except DocumentError as exc:
                rejected.append((filename, str(exc)))
        if accepted:
            documents = self.documents + tuple(accepted)
            self.index = SearchIndex(documents)
            self.documents = documents
            self.reset_answers()
        return rejected

    def ask(self, question):
        question = question.strip()
        if not question or len(question) > MAX_QUESTION:
            raise ValueError("Enter a question between 1 and 400 characters.")
        self.answer = Answer(question, self.index.retrieve(question))
        previous = (item for item in self.history if item.question != question)
        self.history = (self.answer, *previous)[:MAX_HISTORY]
        return self.answer

    def restore(self, answer):
        if answer in self.history:
            self.answer = answer
