"""여기어때 사진 다운로더 - 창(GUI) 프로그램. .exe로 묶여 더블클릭으로 실행된다."""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import yeogi_photos as core

TITLE = "여기어때 사진 다운로더"
SETTINGS = os.path.join(core.DATA_DIR, "settings.json")
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "여기어때 사진")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        os.makedirs(core.DATA_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


class GuiUI:
    """작업 스레드 → 화면 스레드 전달용. tkinter는 화면 스레드에서만 다뤄야 한다."""

    def __init__(self, app):
        self.app = app

    def log(self, msg=""):
        self.app.q.put(("log", msg))

    def progress(self, done, total):
        self.app.q.put(("progress", (done, total)))

    def ask(self, msg):
        done = threading.Event()
        self.app.q.put(("ask", (msg, done)))
        done.wait()


class App:
    def __init__(self, root):
        self.root, self.q, self.worker, self.last_folder = root, queue.Queue(), None, None
        st = load_settings()
        root.title(TITLE)
        root.geometry("680x520")
        root.minsize(560, 420)
        pad = dict(padx=10, pady=4)

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="숙소 링크").grid(row=0, column=0, sticky="w", **pad)
        self.url = tk.StringVar()
        self.url_entry = ttk.Entry(frm, textvariable=self.url)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        self._paste_menu(self.url_entry)

        ttk.Label(frm, text="저장 폴더").grid(row=1, column=0, sticky="w", **pad)
        self.out = tk.StringVar(value=st.get("out", DEFAULT_OUT))
        ttk.Entry(frm, textvariable=self.out).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="폴더 선택", command=self.choose_folder).grid(row=1, column=2, **pad)

        opt = ttk.Frame(frm)
        opt.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(opt, text="예상 사진 수 (선택)").pack(side="left")
        self.expected = tk.StringVar()
        ttk.Entry(opt, textvariable=self.expected, width=5).pack(side="left", padx=(6, 20))
        self.show = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="브라우저 창 보이기", variable=self.show).pack(side="left")

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        self.dl_btn = ttk.Button(btns, text="다운로드", command=self.start)
        self.dl_btn.pack(side="left", ipadx=20, ipady=4)
        self.open_btn = ttk.Button(btns, text="저장 폴더 열기", command=self.open_last, state="disabled")
        self.open_btn.pack(side="left", padx=10, ipady=4)

        self.bar = ttk.Progressbar(frm, mode="determinate")
        self.bar.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        self.status = tk.StringVar(value="숙소 링크를 붙여넣고 [다운로드]를 누르세요.")
        ttk.Label(frm, textvariable=self.status, font=("", 10, "bold")).grid(
            row=5, column=0, columnspan=3, sticky="w", **pad)

        logf = ttk.Frame(frm)
        logf.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(6, weight=1)
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.url_entry.focus_set()
        root.bind("<Return>", lambda e: self.start())
        root.after(100, self.poll)

    def _paste_menu(self, entry):
        m = tk.Menu(entry, tearoff=0)
        m.add_command(label="붙여넣기", command=lambda: entry.event_generate("<<Paste>>"))
        m.add_command(label="모두 지우기", command=lambda: entry.delete(0, "end"))
        entry.bind("<Button-3>", lambda e: m.tk_popup(e.x_root, e.y_root))

    def choose_folder(self):
        d = filedialog.askdirectory(title="사진을 저장할 폴더 선택", initialdir=self.out.get() or DEFAULT_OUT)
        if d:
            self.out.set(os.path.normpath(d))

    def write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        url, out = self.url.get().strip(), self.out.get().strip()
        if not url:
            messagebox.showwarning(TITLE, "숙소 링크를 붙여넣어 주세요.")
            return
        if "yeogi.com" not in url and not messagebox.askyesno(
                TITLE, "여기어때(yeogi.com) 주소가 아닌 것 같습니다. 그래도 진행할까요?"):
            return
        if not out:
            messagebox.showwarning(TITLE, "저장 폴더를 선택해 주세요.")
            return
        exp = self.expected.get().strip()
        expected = int(exp) if exp.isdigit() else None
        save_settings({"out": out})
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.bar.configure(value=0, maximum=1)
        self.dl_btn.configure(state="disabled")
        self.status.set("작업 중... (사진 수에 따라 1~2분 걸릴 수 있습니다)")
        self.worker = threading.Thread(target=self.work, args=(url, out, expected, self.show.get()), daemon=True)
        self.worker.start()

    def work(self, url, out, expected, show):
        try:
            res = core.run(url, out, ui=GuiUI(self), expected=expected, show=show)
            self.q.put(("done", res))
        except core.UserError as e:
            self.q.put(("error", str(e)))
        except Exception as e:  # 예상하지 못한 오류도 창으로 알려준다
            self.q.put(("error", f"오류가 발생했습니다.\n\n{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(val)
                elif kind == "progress":
                    done, total = val
                    self.bar.configure(maximum=max(total, 1), value=done)
                    self.status.set(f"다운로드 중... {done}/{total}")
                elif kind == "ask":
                    msg, ev = val
                    self.status.set("브라우저 창에서 작업이 필요합니다.")
                    self.root.attributes("-topmost", True)
                    messagebox.showinfo(TITLE, msg, parent=self.root)
                    self.root.attributes("-topmost", False)
                    self.status.set("작업 중...")
                    ev.set()
                elif kind == "done":
                    self.finish(val)
                elif kind == "error":
                    self.dl_btn.configure(state="normal")
                    self.status.set("실패했습니다.")
                    self.write(val)
                    messagebox.showerror(TITLE, val)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self, r):
        self.dl_btn.configure(state="normal")
        self.last_folder = r["folder"]
        self.open_btn.configure(state="normal")
        line = f"완료: 수집 {r['collected']}장 · 성공 {r['ok']}장 · 실패 {r['failed']}장"
        self.status.set(line)
        msg = f"{line}\n\n저장 위치:\n{r['folder']}"
        if r["expected"] and r["collected"] != r["expected"]:
            msg += f"\n\n※ 예상 사진 수({r['expected']}장)와 다릅니다.\n'브라우저 창 보이기'를 켜고 다시 확인해 보세요."
            messagebox.showwarning(TITLE, msg)
        elif r["failed"]:
            messagebox.showwarning(TITLE, msg)
        elif messagebox.askyesno(TITLE, msg + "\n\n저장 폴더를 열까요?"):
            self.open_last()

    def open_last(self):
        if self.last_folder and os.path.isdir(self.last_folder):
            open_folder(self.last_folder)


class FileUI(core.ConsoleUI):
    """자동 점검(--selftest)용: 창 없이 로그를 파일에 남긴다."""

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def log(self, msg=""):
        self.f.write(msg + "\n")
        self.f.flush()

    def ask(self, msg):
        raise core.UserError("자동 점검 중에는 사용자 확인을 받을 수 없습니다: " + msg)


def selftest(url, out):
    """빌드된 .exe가 브라우저 제어부터 저장까지 동작하는지 점검 (GitHub Actions에서 사용)."""
    os.makedirs(out, exist_ok=True)
    try:
        res = core.run(url, out, ui=FileUI(os.path.join(out, "selftest.log")))
        res = {k: v for k, v in res.items() if k != "photos"}
        code = 0
    except Exception as e:
        res, code = {"error": f"{type(e).__name__}: {e}"}, 1
    with open(os.path.join(out, "selftest.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    sys.exit(code)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2], sys.argv[3])
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
