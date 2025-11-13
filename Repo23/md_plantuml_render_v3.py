#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
md_plantuml_render_v3.py
------------------------
Renderizza i blocchi PlantUML dentro un Markdown in immagini locali e
sostituisce i blocchi con riferimenti Markdown alle immagini generate.

Migliorie chiave:
- Riconosce fence **indentati** e con lingua "plantuml" o "uml" con opzioni
  (es. ```plantuml {theme=...}).
- Gestisce diagrammi con **titolo**: "@startuml <nome-diagramma>" (usato in alt text
  e nel filename sanificato).
- Sostituisce **interamente** il fenced block quando contiene diagrammi.
- Supporta diagrammi **nudi** (fuori dai fence).
- Tollerante a CRLF/whitespace. Continua anche se un diagramma fallisce.

Uso:
  python md_plantuml_render_v3.py input.md --jar C:\path\plantuml.jar --format png

Requisiti: Java nel PATH, plantuml.jar locale; per alcuni diagrammi serve Graphviz (dot).
"""

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# Fence regex: cattura fence indentati con ``` o ~~~, lingua su stessa riga (qualsiasi testo fino a \n),
# corpo fino al fence di chiusura uguale. MULTILINE + DOTALL per robustezza.
FENCE_BLOCK_RE = re.compile(
    r"(?ms)"                            # DOTALL + MULTILINE
    r"^[ \t]*(?P<fence>```|~~~)[ \t]*(?P<lang>[^\n]*)\n"  # apertura
    r"(?P<body>.*?)"                    # corpo non-greedy
    r"^[ \t]*(?P=fence)[ \t]*\r?\n?",   # chiusura
)

# Trova @startuml ... @enduml (anche con titolo dopo @startuml)
START_END_RE = re.compile(r"@startuml[^\n]*\n.*?@enduml", re.IGNORECASE | re.DOTALL)

TITLE_RE = re.compile(r"@startuml[ \t]+([^\r\n]+)", re.IGNORECASE)

def sha1_10(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

def is_plantuml_lang(lang_raw: str) -> bool:
    lang = (lang_raw or "").strip().lower()
    # accetta "plantuml", "uml", "plantuml {theme=...}", "uml   something"
    return lang.startswith("plantuml") or lang.startswith("uml")

def sanitize_filename(s: str) -> str:
    s = s.strip().lower()
    # sostituisci caratteri non alfanum, punto, trattino o underscore con trattino
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = s.strip("-")
    return s or "diagram"

class Snippet:
    def __init__(self, start: int, end: int, diagrams: List[str]):
        self.start = start
        self.end = end
        self.diagrams = diagrams

def find_snippets(md_text: str) -> List[Snippet]:
    snippets: List[Snippet] = []
    covered = [False] * (len(md_text) + 1)

    # 1) Fenced blocks
    for m in FENCE_BLOCK_RE.finditer(md_text):
        start, end = m.start(), m.end()
        body = m.group("body")
        lang = m.group("lang")

        for i in range(start, end):
            covered[i] = True

        diagrams = [dm.group() for dm in START_END_RE.finditer(body)]
        if diagrams:
            # Sostituisci l'intero fence con tutte le immagini in sequenza
            snippets.append(Snippet(start, end, diagrams))
        else:
            # Se la lingua è plantuml/uml ma senza @startuml, incapsula body
            if is_plantuml_lang(lang) and body.strip():
                diagrams = [f"@startuml\n{body}\n@enduml\n"]
                snippets.append(Snippet(start, end, diagrams))

    # 2) Diagrammi nudi (fuori dai fence)
    for dm in START_END_RE.finditer(md_text):
        s, e = dm.start(), dm.end()
        if not any(covered[s:e]):
            snippets.append(Snippet(s, e, [dm.group()]))

    snippets.sort(key=lambda x: x.start)
    return snippets

def run_plantuml(jar_path: Path, puml_path: Path, fmt: str) -> Tuple[bool, str]:
    cmd = ["java", "-Djava.awt.headless=true", "-jar", str(jar_path), f"-t{fmt}", str(puml_path)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
        ok = (proc.returncode == 0)
        msg = (proc.stdout or "") + (proc.stderr or "")
        return ok, msg.strip()
    except FileNotFoundError:
        return False, "Java non trovato nel PATH. Installa Java o aggiungilo al PATH."
    except Exception as e:
        return False, f"Errore esecuzione PlantUML: {e}"

def extract_title(diagram_text: str) -> str:
    m = TITLE_RE.search(diagram_text)
    if m:
        return m.group(1).strip()
    return ""

def main():
    ap = argparse.ArgumentParser(description="Renderizza diagrammi PlantUML in un Markdown.")
    ap.add_argument("input_md", help="Percorso del file Markdown di input")
    ap.add_argument("--jar", dest="plantuml_jar", required=True, help="Percorso a plantuml.jar")
    ap.add_argument("--format", dest="fmt", default="png", choices=["png", "svg"], help="Formato immagine")
    ap.add_argument("--out", dest="output_md", default=None, help="File .md di output (default: <input>.rendered.md)")
    ap.add_argument("--prefix", dest="img_prefix", default="diagram", help="Prefisso per i nomi immagine")
    args = ap.parse_args()

    in_path = Path(args.input_md).resolve()
    if not in_path.exists():
        print(f"❌ File di input non trovato: {in_path}", file=sys.stderr); sys.exit(1)

    jar_path = Path(args.plantuml_jar).resolve()
    if not jar_path.exists():
        print(f"❌ plantuml.jar non trovato: {jar_path}", file=sys.stderr); sys.exit(1)

    out_path = Path(args.output_md).resolve() if args.output_md else in_path.with_suffix(".rendered.md")
    work_dir = in_path.parent

    try:
        md_text = in_path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        print(f"❌ Impossibile leggere il Markdown: {e}", file=sys.stderr); sys.exit(1)

    snippets = find_snippets(md_text)
    if not snippets:
        print("ℹ️ Nessun blocco PlantUML trovato. Copio invariato.")
        out_path.write_text(md_text, encoding="utf-8")
        print(f"✅ Output: {out_path}"); sys.exit(0)

    new_md = []
    cursor = 0
    diagram_counter = 1
    log_lines = []
    created = []

    for sn in snippets:
        new_md.append(md_text[cursor:sn.start])
        # Genera immagini per i diagrammi dello snippet
        images_md = []
        for diag in sn.diagrams:
            title = extract_title(diag)
            slug = sanitize_filename(title) if title else None
            digest = sha1_10(diag)
            base_name = f"{args.img_prefix}_{diagram_counter:03d}_{digest}"
            if slug:
                base_name = f"{base_name}_{slug}"

            puml_path = work_dir / f"{base_name}.puml"
            img_path = work_dir / f"{base_name}.{args.fmt}"

            try:
                puml_path.write_text(diag, encoding="utf-8")
            except Exception as e:
                log_lines.append(f"❌ Errore scrivendo {puml_path.name}: {e}")
                images_md = [md_text[sn.start:sn.end]]  # mantieni originale
                break

            ok, msg = run_plantuml(jar_path, puml_path, args.fmt)
            if ok and img_path.exists():
                alt = title if title else base_name
                images_md.append(f"![{alt}]({img_path.name})\n")
                created.append(img_path.name)
                log_lines.append(f"✅ {puml_path.name} → {img_path.name}")
            else:
                log_lines.append(f"❌ Errore su {puml_path.name}:\n{msg or 'Errore sconosciuto.'}")
                images_md = [md_text[sn.start:sn.end]]  # mantieni originale
                break

            diagram_counter += 1

        new_md.append("".join(images_md))
        cursor = sn.end

    new_md.append(md_text[cursor:])

    try:
        out_path.write_text("".join(new_md), encoding="utf-8")
    except Exception as e:
        print(f"❌ Impossibile scrivere l'output Markdown: {e}", file=sys.stderr); sys.exit(1)

    print("\n".join(log_lines))
    print(f"\n✅ File Markdown generato: {out_path}")
    if created:
        print("🖼️ Immagini create:")
        for n in created:
            print("   -", n)
    else:
        print("ℹ️ Nessuna immagine generata (possibili errori).")

if __name__ == "__main__":
    main()
