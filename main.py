import hashlib
import secrets
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="DuitKu")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NAMA_DB = "duitku.db"


def sambung():
    """Buka sambungan ke pangkalan data."""
    conn = sqlite3.connect(NAMA_DB)
    conn.row_factory = sqlite3.Row
    return conn


def sedia_db():
    """Cipta semua jadual jika belum wujud dan kemas kini lajur pengguna_id."""
    conn = sambung()

    # Jadual pengguna & sesi log masuk
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pengguna (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama_paparan TEXT NOT NULL,
            nama_pengguna TEXT NOT NULL UNIQUE COLLATE NOCASE,
            garam TEXT NOT NULL,
            kata_laluan_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sesi (
            token TEXT PRIMARY KEY,
            pengguna_id INTEGER NOT NULL
        )
        """
    )

    # Jadual utama aplikasi
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS akaun (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL DEFAULT 1,
            nama TEXT NOT NULL,
            jenis TEXT NOT NULL,
            baki_awal REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL DEFAULT 1,
            jenis TEXT NOT NULL,
            jumlah REAL NOT NULL,
            kategori TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            tarikh TEXT NOT NULL,
            akaun_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS matlamat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL DEFAULT 1,
            nama TEXT NOT NULL,
            sasaran REAL NOT NULL,
            terkumpul REAL NOT NULL DEFAULT 0,
            tarikh_sasaran TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS berulang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL DEFAULT 1,
            jenis TEXT NOT NULL,
            jumlah REAL NOT NULL,
            kategori TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            akaun_id INTEGER,
            hari INTEGER NOT NULL,
            aktif INTEGER NOT NULL DEFAULT 1,
            bulan_terakhir TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tugasan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL DEFAULT 1,
            tajuk TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            masa_peringatan TEXT,
            gambar TEXT,
            selesai INTEGER NOT NULL DEFAULT 0,
            sudah_beritahu INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # Kemas kini jadual lama jika pernah dicipta sebelum sistem login
    for jadual in ["akaun", "transaksi", "matlamat", "berulang", "tugasan"]:
        lajur = [r["name"] for r in conn.execute(f"PRAGMA table_info({jadual})")]
        if "pengguna_id" not in lajur:
            conn.execute(
                f"ALTER TABLE {jadual} ADD COLUMN pengguna_id INTEGER NOT NULL DEFAULT 1"
            )

    lajur_tx = [r["name"] for r in conn.execute("PRAGMA table_info(transaksi)")]
    if "akaun_id" not in lajur_tx:
        conn.execute("ALTER TABLE transaksi ADD COLUMN akaun_id INTEGER")

    lajur_td = [r["name"] for r in conn.execute("PRAGMA table_info(tugasan)")]
    if "gambar" not in lajur_td:
        conn.execute("ALTER TABLE tugasan ADD COLUMN gambar TEXT")

    # Jadual bajet: unik mengikut gabungan pengguna_id + kategori
    sql_bajet = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='bajet'"
    ).fetchone()
    if sql_bajet and "pengguna_id" not in sql_bajet["sql"]:
        conn.execute("ALTER TABLE bajet RENAME TO bajet_lama")
        conn.execute("DROP TABLE bajet_lama")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bajet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pengguna_id INTEGER NOT NULL,
            kategori TEXT NOT NULL COLLATE NOCASE,
            had_bulanan REAL NOT NULL,
            UNIQUE(pengguna_id, kategori)
        )
        """
    )

    conn.commit()
    conn.close()


sedia_db()


# ---------- Fungsi Keselamatan & Pengesahan ----------

def cincang_kata_laluan(kata_laluan: str, garam: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        kata_laluan.encode("utf-8"),
        garam.encode("utf-8"),
        100_000,
    ).hex()


def cipta_sesi(conn, pengguna_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sesi (token, pengguna_id) VALUES (?, ?)",
        (token, pengguna_id),
    )
    return token


def dapatkan_pengguna_semasa(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sila log masuk dahulu")
    token = authorization.split(" ", 1)[1].strip()
    conn = sambung()
    baris = conn.execute(
        """
        SELECT p.id, p.nama_paparan, p.nama_pengguna
        FROM sesi s
        JOIN pengguna p ON p.id = s.pengguna_id
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    conn.close()
    if baris is None:
        raise HTTPException(
            status_code=401, detail="Sesi tidak sah atau telah tamat. Sila log masuk semula."
        )
    return {"id": baris["id"], "nama_paparan": baris["nama_paparan"], "nama_pengguna": baris["nama_pengguna"], "token": token}


# ---------- Bentuk Data (Pydantic) ----------

class DaftarBaru(BaseModel):
    nama_paparan: str
    nama_pengguna: str
    kata_laluan: str


class LogMasukData(BaseModel):
    nama_pengguna: str
    kata_laluan: str


class AkaunBaru(BaseModel):
    nama: str
    jenis: Literal["tunai", "bank", "ewallet"]
    baki_awal: float = 0


class TransaksiBaru(BaseModel):
    jumlah: float
    kategori: str
    catatan: str = ""
    akaun_id: Optional[int] = None


class TransaksiKemaskini(BaseModel):
    jumlah: Optional[float] = None
    kategori: Optional[str] = None
    catatan: Optional[str] = None
    akaun_id: Optional[int] = None


class BajetBaru(BaseModel):
    kategori: str
    had_bulanan: float


class MatlamatBaru(BaseModel):
    nama: str
    sasaran: float
    tarikh_sasaran: Optional[str] = None


class Simpanan(BaseModel):
    jumlah: float


class BerulangBaru(BaseModel):
    jenis: Literal["belanja", "pendapatan"]
    jumlah: float
    kategori: str
    catatan: str = ""
    akaun_id: Optional[int] = None
    hari: int


class TugasanBaru(BaseModel):
    tajuk: str
    catatan: str = ""
    masa_peringatan: Optional[str] = None
    gambar: Optional[str] = None


# ---------- Laluan: Umum & Pengesahan ----------

@app.get("/")
def utama():
    fail_html = Path(__file__).parent / "index.html"
    if fail_html.exists():
        return FileResponse(fail_html)
    return {"mesej": "DuitKu sedang berjalan"}


@app.post("/daftar")
def daftar_pengguna(data: DaftarBaru):
    nama_paparan = data.nama_paparan.strip()
    nama_pengguna = data.nama_pengguna.strip().lower()
    if len(nama_paparan) < 2:
        raise HTTPException(status_code=400, detail="Nama paparan mesti sekurang-kurangnya 2 aksara")
    if len(nama_pengguna) < 3 or " " in nama_pengguna:
        raise HTTPException(status_code=400, detail="Nama pengguna mesti sekurang-kurangnya 3 aksara tanpa jarak")
    if len(data.kata_laluan) < 4:
        raise HTTPException(status_code=400, detail="Kata laluan mesti sekurang-kurangnya 4 aksara")

    conn = sambung()
    sedia_ada = conn.execute(
        "SELECT id FROM pengguna WHERE nama_pengguna = ?", (nama_pengguna,)
    ).fetchone()
    if sedia_ada:
        conn.close()
        raise HTTPException(status_code=400, detail="Nama pengguna sudah digunakan. Pilih nama lain.")

    garam = secrets.token_hex(16)
    hash_kl = cincang_kata_laluan(data.kata_laluan, garam)

    kursor = conn.execute(
        "INSERT INTO pengguna (nama_paparan, nama_pengguna, garam, kata_laluan_hash) VALUES (?, ?, ?, ?)",
        (nama_paparan, nama_pengguna, garam, hash_kl),
    )
    pengguna_id = kursor.lastrowid
    token = cipta_sesi(conn, pengguna_id)
    conn.commit()
    conn.close()

    return {
        "token": token,
        "pengguna": {
            "id": pengguna_id,
            "nama_paparan": nama_paparan,
            "nama_pengguna": nama_pengguna,
        },
    }


@app.post("/log-masuk")
def log_masuk(data: LogMasukData):
    nama_pengguna = data.nama_pengguna.strip().lower()
    conn = sambung()
    baris = conn.execute(
        "SELECT * FROM pengguna WHERE nama_pengguna = ?", (nama_pengguna,)
    ).fetchone()

    if baris is None:
        conn.close()
        raise HTTPException(status_code=401, detail="Nama pengguna atau kata laluan salah")

    hash_semak = cincang_kata_laluan(data.kata_laluan, baris["garam"])
    if not secrets.compare_digest(hash_semak, baris["kata_laluan_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Nama pengguna atau kata laluan salah")

    token = cipta_sesi(conn, baris["id"])
    conn.commit()
    conn.close()

    return {
        "token": token,
        "pengguna": {
            "id": baris["id"],
            "nama_paparan": baris["nama_paparan"],
            "nama_pengguna": baris["nama_pengguna"],
        },
    }


@app.post("/log-keluar")
def log_keluar(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    conn.execute("DELETE FROM sesi WHERE token = ?", (user["token"],))
    conn.commit()
    conn.close()
    return {"mesej": "Berjaya log keluar"}


@app.get("/profil")
def lihat_profil(user=Depends(dapatkan_pengguna_semasa)):
    return {
        "id": user["id"],
        "nama_paparan": user["nama_paparan"],
        "nama_pengguna": user["nama_pengguna"],
    }


# ---------- Fungsi Bantu ----------

def semak_akaun_wujud(conn, akaun_id: Optional[int], pengguna_id: int):
    if akaun_id is None:
        return
    ada = conn.execute(
        "SELECT id FROM akaun WHERE id = ? AND pengguna_id = ?",
        (akaun_id, pengguna_id),
    ).fetchone()
    if ada is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Akaun tidak dijumpai")


def simpan(jenis: str, data: TransaksiBaru, pengguna_id: int):
    if data.jumlah <= 0:
        raise HTTPException(status_code=400, detail="Jumlah mesti lebih daripada 0")
    conn = sambung()
    semak_akaun_wujud(conn, data.akaun_id, pengguna_id)
    tarikh_hari_ini = str(date.today())
    kursor = conn.execute(
        "INSERT INTO transaksi (pengguna_id, jenis, jumlah, kategori, catatan, tarikh, akaun_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pengguna_id, jenis, data.jumlah, data.kategori, data.catatan, tarikh_hari_ini, data.akaun_id),
    )
    conn.commit()
    id_baru = kursor.lastrowid
    conn.close()
    return {
        "id": id_baru,
        "jenis": jenis,
        "jumlah": data.jumlah,
        "kategori": data.kategori,
        "catatan": data.catatan,
        "tarikh": tarikh_hari_ini,
        "akaun_id": data.akaun_id,
    }


def senarai_ikut_jenis(jenis: str, pengguna_id: int):
    conn = sambung()
    baris = conn.execute(
        "SELECT id, jenis, jumlah, kategori, catatan, tarikh, akaun_id "
        "FROM transaksi WHERE jenis = ? AND pengguna_id = ? ORDER BY id",
        (jenis, pengguna_id),
    ).fetchall()
    conn.close()
    senarai = [dict(r) for r in baris]
    jumlah_semua = round(sum(r["jumlah"] for r in senarai), 2)
    return {"jumlah_semua": jumlah_semua, "senarai": senarai}


# ---------- Laluan: Akaun ----------

@app.post("/akaun")
def tambah_akaun(a: AkaunBaru, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "INSERT INTO akaun (pengguna_id, nama, jenis, baki_awal) VALUES (?, ?, ?, ?)",
        (user["id"], a.nama, a.jenis, a.baki_awal),
    )
    conn.commit()
    id_baru = kursor.lastrowid
    conn.close()
    return {"id": id_baru, "nama": a.nama, "jenis": a.jenis, "baki_awal": a.baki_awal}


@app.get("/akaun")
def lihat_akaun(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    baris = conn.execute(
        """
        SELECT a.id, a.nama, a.jenis, a.baki_awal,
               COALESCE(SUM(
                   CASE WHEN t.jenis = 'pendapatan' THEN t.jumlah
                        WHEN t.jenis = 'belanja' THEN -t.jumlah
                        ELSE 0 END
               ), 0) AS pergerakan
        FROM akaun a
        LEFT JOIN transaksi t ON t.akaun_id = a.id AND t.pengguna_id = ?
        WHERE a.pengguna_id = ?
        GROUP BY a.id
        ORDER BY a.id
        """,
        (user["id"], user["id"]),
    ).fetchall()
    conn.close()
    senarai = []
    for r in baris:
        senarai.append({
            "id": r["id"],
            "nama": r["nama"],
            "jenis": r["jenis"],
            "baki_awal": r["baki_awal"],
            "baki_semasa": round(r["baki_awal"] + r["pergerakan"], 2),
        })
    jumlah_semua = round(sum(a["baki_semasa"] for a in senarai), 2)
    return {"jumlah_semua_akaun": jumlah_semua, "senarai": senarai}


@app.delete("/akaun/{id_akaun}")
def padam_akaun(id_akaun: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    bilangan = conn.execute(
        "SELECT COUNT(*) AS n FROM transaksi WHERE akaun_id = ? AND pengguna_id = ?",
        (id_akaun, user["id"]),
    ).fetchone()["n"]
    if bilangan > 0:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Akaun ini masih ada transaksi. Padam transaksi dahulu.",
        )
    kursor = conn.execute(
        "DELETE FROM akaun WHERE id = ? AND pengguna_id = ?",
        (id_akaun, user["id"]),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Akaun tidak dijumpai")
    return {"mesej": f"Akaun {id_akaun} telah dipadam"}


# ---------- Laluan: Transaksi & Baki ----------

@app.post("/belanja")
def tambah_belanja(b: TransaksiBaru, user=Depends(dapatkan_pengguna_semasa)):
    return simpan("belanja", b, user["id"])


@app.get("/belanja")
def lihat_belanja(user=Depends(dapatkan_pengguna_semasa)):
    return senarai_ikut_jenis("belanja", user["id"])


@app.post("/pendapatan")
def tambah_pendapatan(p: TransaksiBaru, user=Depends(dapatkan_pengguna_semasa)):
    return simpan("pendapatan", p, user["id"])


@app.get("/pendapatan")
def lihat_pendapatan(user=Depends(dapatkan_pengguna_semasa)):
    return senarai_ikut_jenis("pendapatan", user["id"])


@app.get("/baki")
def lihat_baki(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    baki_awal = conn.execute(
        "SELECT COALESCE(SUM(baki_awal), 0) AS n FROM akaun WHERE pengguna_id = ?",
        (user["id"],),
    ).fetchone()["n"]
    conn.close()
    jumlah_pendapatan = senarai_ikut_jenis("pendapatan", user["id"])["jumlah_semua"]
    jumlah_belanja = senarai_ikut_jenis("belanja", user["id"])["jumlah_semua"]
    return {
        "baki_awal_akaun": round(baki_awal, 2),
        "pendapatan": jumlah_pendapatan,
        "belanja": jumlah_belanja,
        "baki": round(baki_awal + jumlah_pendapatan - jumlah_belanja, 2),
    }


@app.put("/transaksi/{id_transaksi}")
def kemaskini_transaksi(id_transaksi: int, data: TransaksiKemaskini, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    sedia_ada = conn.execute(
        "SELECT * FROM transaksi WHERE id = ? AND pengguna_id = ?",
        (id_transaksi, user["id"]),
    ).fetchone()
    if sedia_ada is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Transaksi tidak dijumpai")

    semak_akaun_wujud(conn, data.akaun_id, user["id"])

    jumlah = data.jumlah if data.jumlah is not None else sedia_ada["jumlah"]
    if jumlah <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Jumlah mesti lebih daripada 0")

    kategori = data.kategori if data.kategori is not None else sedia_ada["kategori"]
    catatan = data.catatan if data.catatan is not None else sedia_ada["catatan"]
    akaun_id = data.akaun_id if data.akaun_id is not None else sedia_ada["akaun_id"]

    conn.execute(
        "UPDATE transaksi SET jumlah = ?, kategori = ?, catatan = ?, akaun_id = ? "
        "WHERE id = ? AND pengguna_id = ?",
        (jumlah, kategori, catatan, akaun_id, id_transaksi, user["id"]),
    )
    conn.commit()
    baru = conn.execute(
        "SELECT * FROM transaksi WHERE id = ?", (id_transaksi,)
    ).fetchone()
    conn.close()
    return dict(baru)


@app.delete("/transaksi/{id_transaksi}")
def padam_transaksi(id_transaksi: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM transaksi WHERE id = ? AND pengguna_id = ?",
        (id_transaksi, user["id"]),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaksi tidak dijumpai")
    return {"mesej": f"Transaksi {id_transaksi} telah dipadam"}


@app.get("/transaksi")
def cari_transaksi(
    jenis: Optional[str] = None,
    kategori: Optional[str] = None,
    akaun_id: Optional[int] = None,
    dari: Optional[str] = None,
    hingga: Optional[str] = None,
    kata_kunci: Optional[str] = None,
    user=Depends(dapatkan_pengguna_semasa),
):
    syarat = ["pengguna_id = ?"]
    nilai = [user["id"]]

    if jenis:
        syarat.append("jenis = ?")
        nilai.append(jenis)
    if kategori:
        syarat.append("kategori = ? COLLATE NOCASE")
        nilai.append(kategori)
    if akaun_id is not None:
        syarat.append("akaun_id = ?")
        nilai.append(akaun_id)
    if dari:
        syarat.append("tarikh >= ?")
        nilai.append(dari)
    if hingga:
        syarat.append("tarikh <= ?")
        nilai.append(hingga)
    if kata_kunci:
        syarat.append("(catatan LIKE ? OR kategori LIKE ?)")
        nilai.append("%" + kata_kunci + "%")
        nilai.append("%" + kata_kunci + "%")

    sql = (
        "SELECT id, jenis, jumlah, kategori, catatan, tarikh, akaun_id "
        "FROM transaksi WHERE " + " AND ".join(syarat) + " ORDER BY tarikh DESC, id DESC"
    )

    conn = sambung()
    baris = conn.execute(sql, nilai).fetchall()
    conn.close()

    senarai = [dict(r) for r in baris]
    jumlah_pendapatan = round(sum(r["jumlah"] for r in senarai if r["jenis"] == "pendapatan"), 2)
    jumlah_belanja = round(sum(r["jumlah"] for r in senarai if r["jenis"] == "belanja"), 2)
    return {
        "bilangan": len(senarai),
        "jumlah_pendapatan": jumlah_pendapatan,
        "jumlah_belanja": jumlah_belanja,
        "senarai": senarai,
    }


# ---------- Laluan: Bajet & Ringkasan ----------

def tentukan_status(had, belanja):
    peratus = round(belanja / had * 100, 1) if had > 0 else 0
    if peratus >= 100:
        return peratus, "melebihi", "Bajet sudah melebihi had!"
    elif peratus >= 80:
        return peratus, "hampir habis", "Awas, bajet hampir habis."
    return peratus, "selamat", ""


@app.post("/bajet")
def tetapkan_bajet(b: BajetBaru, user=Depends(dapatkan_pengguna_semasa)):
    if b.had_bulanan <= 0:
        raise HTTPException(status_code=400, detail="Had mesti lebih daripada 0")
    conn = sambung()
    conn.execute(
        "INSERT INTO bajet (pengguna_id, kategori, had_bulanan) VALUES (?, ?, ?) "
        "ON CONFLICT(pengguna_id, kategori) DO UPDATE SET had_bulanan = excluded.had_bulanan",
        (user["id"], b.kategori, b.had_bulanan),
    )
    conn.commit()
    conn.close()
    return {"kategori": b.kategori, "had_bulanan": b.had_bulanan}


@app.get("/bajet")
def lihat_bajet(bulan: Optional[str] = None, user=Depends(dapatkan_pengguna_semasa)):
    if bulan is None:
        bulan = str(date.today())[:7]

    conn = sambung()
    senarai_bajet = conn.execute(
        "SELECT kategori, had_bulanan FROM bajet WHERE pengguna_id = ? ORDER BY kategori",
        (user["id"],),
    ).fetchall()

    hasil = []
    for rekod in senarai_bajet:
        baris = conn.execute(
            "SELECT COALESCE(SUM(jumlah), 0) AS n FROM transaksi "
            "WHERE pengguna_id = ? AND jenis = 'belanja' AND kategori = ? COLLATE NOCASE "
            "AND tarikh LIKE ?",
            (user["id"], rekod["kategori"], bulan + "%"),
        ).fetchone()
        belanja = round(baris["n"], 2)
        had = rekod["had_bulanan"]
        peratus, status, amaran = tentukan_status(had, belanja)
        hasil.append({
            "kategori": rekod["kategori"],
            "had_bulanan": had,
            "telah_belanja": belanja,
            "baki_bajet": round(had - belanja, 2),
            "peratus_guna": peratus,
            "status": status,
            "amaran": amaran,
        })
    conn.close()
    return {"bulan": bulan, "senarai": hasil}


@app.delete("/bajet/{kategori}")
def padam_bajet(kategori: str, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM bajet WHERE pengguna_id = ? AND kategori = ? COLLATE NOCASE",
        (user["id"], kategori),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bajet tidak dijumpai")
    return {"mesej": f"Bajet {kategori} telah dipadam"}


# ---------- Laluan: Matlamat Simpanan ----------

def butiran_matlamat(r):
    sasaran = r["sasaran"]
    terkumpul = r["terkumpul"]
    baki = round(max(sasaran - terkumpul, 0), 2)
    peratus = round(terkumpul / sasaran * 100, 1) if sasaran > 0 else 0

    status = "sedang berjalan"
    perlu_sebulan = None
    bulan_lagi = None

    if terkumpul >= sasaran:
        status = "tercapai"
    elif r["tarikh_sasaran"]:
        sasaran_tarikh = date.fromisoformat(r["tarikh_sasaran"])
        hari_ini = date.today()
        if sasaran_tarikh < hari_ini:
            status = "lepas tarikh"
        else:
            bulan_lagi = (sasaran_tarikh.year - hari_ini.year) * 12 + (sasaran_tarikh.month - hari_ini.month)
            if bulan_lagi < 1:
                bulan_lagi = 1
            perlu_sebulan = round(baki / bulan_lagi, 2)

    return {
        "id": r["id"],
        "nama": r["nama"],
        "sasaran": sasaran,
        "terkumpul": terkumpul,
        "baki": baki,
        "peratus": peratus,
        "tarikh_sasaran": r["tarikh_sasaran"],
        "bulan_lagi": bulan_lagi,
        "perlu_simpan_sebulan": perlu_sebulan,
        "status": status,
    }


def ambil_matlamat(conn, id_matlamat: int, pengguna_id: int):
    r = conn.execute(
        "SELECT * FROM matlamat WHERE id = ? AND pengguna_id = ?",
        (id_matlamat, pengguna_id),
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Matlamat tidak dijumpai")
    return r


@app.post("/matlamat")
def tambah_matlamat(m: MatlamatBaru, user=Depends(dapatkan_pengguna_semasa)):
    if m.sasaran <= 0:
        raise HTTPException(status_code=400, detail="Sasaran mesti lebih daripada 0")
    if m.tarikh_sasaran:
        try:
            date.fromisoformat(m.tarikh_sasaran)
        except ValueError:
            raise HTTPException(status_code=400, detail="Format tarikh mesti YYYY-MM-DD")

    conn = sambung()
    kursor = conn.execute(
        "INSERT INTO matlamat (pengguna_id, nama, sasaran, tarikh_sasaran) VALUES (?, ?, ?, ?)",
        (user["id"], m.nama, m.sasaran, m.tarikh_sasaran),
    )
    conn.commit()
    r = ambil_matlamat(conn, kursor.lastrowid, user["id"])
    conn.close()
    return butiran_matlamat(r)


@app.get("/matlamat")
def lihat_matlamat(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    baris = conn.execute(
        "SELECT * FROM matlamat WHERE pengguna_id = ? ORDER BY id", (user["id"],)
    ).fetchall()
    conn.close()
    senarai = [butiran_matlamat(r) for r in baris]
    return {"bilangan": len(senarai), "senarai": senarai}


@app.post("/matlamat/{id_matlamat}/simpan")
def tambah_simpanan(id_matlamat: int, s: Simpanan, user=Depends(dapatkan_pengguna_semasa)):
    if s.jumlah <= 0:
        raise HTTPException(status_code=400, detail="Jumlah simpanan mesti lebih daripada 0")
    conn = sambung()
    ambil_matlamat(conn, id_matlamat, user["id"])
    conn.execute(
        "UPDATE matlamat SET terkumpul = round(terkumpul + ?, 2) WHERE id = ? AND pengguna_id = ?",
        (s.jumlah, id_matlamat, user["id"]),
    )
    conn.commit()
    r = ambil_matlamat(conn, id_matlamat, user["id"])
    conn.close()
    return butiran_matlamat(r)


@app.delete("/matlamat/{id_matlamat}")
def padam_matlamat(id_matlamat: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM matlamat WHERE id = ? AND pengguna_id = ?",
        (id_matlamat, user["id"]),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Matlamat tidak dijumpai")
    return {"mesej": f"Matlamat {id_matlamat} telah dipadam"}


# ---------- Laluan: Belanja Berulang ----------

def ambil_berulang(conn, id_berulang: int, pengguna_id: int):
    r = conn.execute(
        "SELECT * FROM berulang WHERE id = ? AND pengguna_id = ?",
        (id_berulang, pengguna_id),
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Rekod berulang tidak dijumpai")
    return r


def huraian_berulang(r):
    return {
        "id": r["id"],
        "jenis": r["jenis"],
        "jumlah": r["jumlah"],
        "kategori": r["kategori"],
        "catatan": r["catatan"],
        "akaun_id": r["akaun_id"],
        "hari": r["hari"],
        "aktif": bool(r["aktif"]),
        "bulan_terakhir_dijana": r["bulan_terakhir"],
    }


@app.post("/berulang")
def tambah_berulang(b: BerulangBaru, user=Depends(dapatkan_pengguna_semasa)):
    if b.jumlah <= 0:
        raise HTTPException(status_code=400, detail="Jumlah mesti lebih daripada 0")
    if b.hari < 1 or b.hari > 28:
        raise HTTPException(status_code=400, detail="Hari mesti antara 1 hingga 28")
    conn = sambung()
    semak_akaun_wujud(conn, b.akaun_id, user["id"])
    kursor = conn.execute(
        "INSERT INTO berulang (pengguna_id, jenis, jumlah, kategori, catatan, akaun_id, hari) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], b.jenis, b.jumlah, b.kategori, b.catatan, b.akaun_id, b.hari),
    )
    conn.commit()
    r = ambil_berulang(conn, kursor.lastrowid, user["id"])
    conn.close()
    return huraian_berulang(r)


@app.get("/berulang")
def lihat_berulang(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    baris = conn.execute(
        "SELECT * FROM berulang WHERE pengguna_id = ? ORDER BY hari, id",
        (user["id"],),
    ).fetchall()
    conn.close()
    senarai = [huraian_berulang(r) for r in baris]
    return {"bilangan": len(senarai), "senarai": senarai}


@app.post("/berulang/jana")
def jana_berulang(user=Depends(dapatkan_pengguna_semasa)):
    hari_ini = date.today()
    bulan_ini = str(hari_ini)[:7]
    conn = sambung()
    baris = conn.execute(
        """
        SELECT * FROM berulang
        WHERE pengguna_id = ?
          AND aktif = 1
          AND hari <= ?
          AND (bulan_terakhir IS NULL OR bulan_terakhir != ?)
        """,
        (user["id"], hari_ini.day, bulan_ini),
    ).fetchall()

    dijana = []
    for r in baris:
        tarikh_tx = f"{bulan_ini}-{r['hari']:02d}"
        kursor = conn.execute(
            "INSERT INTO transaksi (pengguna_id, jenis, jumlah, kategori, catatan, tarikh, akaun_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], r["jenis"], r["jumlah"], r["kategori"], r["catatan"], tarikh_tx, r["akaun_id"]),
        )
        conn.execute(
            "UPDATE berulang SET bulan_terakhir = ? WHERE id = ?",
            (bulan_ini, r["id"]),
        )
        dijana.append({"transaksi_id": kursor.lastrowid, "kategori": r["kategori"]})

    conn.commit()
    conn.close()
    return {"bulan": bulan_ini, "bilangan_dijana": len(dijana), "senarai": dijana}


@app.patch("/berulang/{id_berulang}/togol")
def togol_berulang(id_berulang: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    r = ambil_berulang(conn, id_berulang, user["id"])
    status_baru = 0 if r["aktif"] else 1
    conn.execute(
        "UPDATE berulang SET aktif = ? WHERE id = ? AND pengguna_id = ?",
        (status_baru, id_berulang, user["id"]),
    )
    conn.commit()
    r_baru = ambil_berulang(conn, id_berulang, user["id"])
    conn.close()
    return huraian_berulang(r_baru)


@app.delete("/berulang/{id_berulang}")
def padam_berulang(id_berulang: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM berulang WHERE id = ? AND pengguna_id = ?",
        (id_berulang, user["id"]),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Rekod berulang tidak dijumpai")
    return {"mesej": f"Rekod berulang {id_berulang} telah dipadam"}


# ---------- Laluan: To-Do List & Peringatan ----------

def semak_format_masa(masa: str):
    try:
        datetime.strptime(masa, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Format masa_peringatan mesti YYYY-MM-DD HH:MM",
        )


def huraian_tugasan(r):
    return {
        "id": r["id"],
        "tajuk": r["tajuk"],
        "catatan": r["catatan"],
        "masa_peringatan": r["masa_peringatan"],
        "gambar": r["gambar"],
        "selesai": bool(r["selesai"]),
        "sudah_beritahu": bool(r["sudah_beritahu"]),
    }


@app.post("/tugasan")
def tambah_tugasan(t: TugasanBaru, user=Depends(dapatkan_pengguna_semasa)):
    if not t.tajuk.strip():
        raise HTTPException(status_code=400, detail="Tajuk tugasan tidak boleh kosong")
    if t.masa_peringatan:
        semak_format_masa(t.masa_peringatan)

    conn = sambung()
    kursor = conn.execute(
        "INSERT INTO tugasan (pengguna_id, tajuk, catatan, masa_peringatan, gambar) VALUES (?, ?, ?, ?, ?)",
        (user["id"], t.tajuk.strip(), t.catatan, t.masa_peringatan, t.gambar),
    )
    conn.commit()
    r = conn.execute("SELECT * FROM tugasan WHERE id = ?", (kursor.lastrowid,)).fetchone()
    conn.close()
    return huraian_tugasan(r)


@app.get("/tugasan")
def lihat_tugasan(user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    baris = conn.execute(
        "SELECT * FROM tugasan WHERE pengguna_id = ? ORDER BY selesai ASC, masa_peringatan ASC, id DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    senarai = [huraian_tugasan(r) for r in baris]
    return {"bilangan": len(senarai), "senarai": senarai}


@app.get("/tugasan/peringatan")
def semak_peringatan(user=Depends(dapatkan_pengguna_semasa)):
    sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sambung()
    baris = conn.execute(
        """
        SELECT * FROM tugasan
        WHERE pengguna_id = ?
          AND selesai = 0
          AND sudah_beritahu = 0
          AND masa_peringatan IS NOT NULL
          AND masa_peringatan <= ?
        ORDER BY masa_peringatan ASC
        """,
        (user["id"], sekarang),
    ).fetchall()

    senarai = [huraian_tugasan(r) for r in baris]

    if senarai:
        ids = [r["id"] for r in baris]
        tanda_soal = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE tugasan SET sudah_beritahu = 1 WHERE id IN ({tanda_soal})",
            ids,
        )
        conn.commit()

    conn.close()
    return {
        "masa_semasa": sekarang,
        "bilangan_notifikasi": len(senarai),
        "notifikasi": senarai,
    }


@app.patch("/tugasan/{id_tugasan}/selesai")
def togol_tugasan_selesai(id_tugasan: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    r = conn.execute(
        "SELECT * FROM tugasan WHERE id = ? AND pengguna_id = ?",
        (id_tugasan, user["id"]),
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Tugasan tidak dijumpai")

    status_baru = 0 if r["selesai"] else 1
    conn.execute(
        "UPDATE tugasan SET selesai = ? WHERE id = ? AND pengguna_id = ?",
        (status_baru, id_tugasan, user["id"]),
    )
    conn.commit()
    r_baru = conn.execute("SELECT * FROM tugasan WHERE id = ?", (id_tugasan,)).fetchone()
    conn.close()
    return huraian_tugasan(r_baru)


@app.delete("/tugasan/{id_tugasan}")
def padam_tugasan(id_tugasan: int, user=Depends(dapatkan_pengguna_semasa)):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM tugasan WHERE id = ? AND pengguna_id = ?",
        (id_tugasan, user["id"]),
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Tugasan tidak dijumpai")
    return {"mesej": f"Tugasan {id_tugasan} telah dipadam"}