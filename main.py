import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # <-- Tambah baris ini

app = FastAPI(title="DuitKu")

# <-- Tambah blok ini supaya index.html boleh berhubung dengan FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from datetime import date, datetime
from datetime import date
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="DuitKu")

NAMA_DB = "duitku.db"


def sambung():
    """Buka sambungan ke pangkalan data."""
    conn = sqlite3.connect(NAMA_DB)
    conn.row_factory = sqlite3.Row
    return conn


def sedia_db():
    """Cipta jadual jika belum wujud, dan tambah lajur baharu jika perlu."""
    conn = sambung()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaksi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jenis TEXT NOT NULL,
            jumlah REAL NOT NULL,
            kategori TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            tarikh TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS akaun (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            jenis TEXT NOT NULL,
            baki_awal REAL NOT NULL DEFAULT 0
        )
        """
    )
    # Tambah lajur akaun_id pada jadual lama (hanya sekali)
    lajur = [r["name"] for r in conn.execute("PRAGMA table_info(transaksi)")]
    if "akaun_id" not in lajur:
        conn.execute("ALTER TABLE transaksi ADD COLUMN akaun_id INTEGER")
    conn.commit()
    conn.close()


sedia_db()


# ---------- Bentuk data ----------

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
    # Semua pilihan: hanya isi yang nak diubah
    jumlah: Optional[float] = None
    kategori: Optional[str] = None
    catatan: Optional[str] = None
    akaun_id: Optional[int] = None


# ---------- Fungsi bantu ----------

def semak_akaun_wujud(conn, akaun_id):
    """Pastikan akaun dengan id itu wujud."""
    if akaun_id is None:
        return
    ada = conn.execute(
        "SELECT id FROM akaun WHERE id = ?", (akaun_id,)
    ).fetchone()
    if ada is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Akaun tidak dijumpai")


def simpan(jenis: str, data: TransaksiBaru):
    conn = sambung()
    semak_akaun_wujud(conn, data.akaun_id)
    kursor = conn.execute(
        "INSERT INTO transaksi (jenis, jumlah, kategori, catatan, tarikh, akaun_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (jenis, data.jumlah, data.kategori, data.catatan,
         str(date.today()), data.akaun_id),
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
        "tarikh": str(date.today()),
        "akaun_id": data.akaun_id,
    }


def senarai_ikut_jenis(jenis: str):
    conn = sambung()
    baris = conn.execute(
        "SELECT id, jenis, jumlah, kategori, catatan, tarikh, akaun_id "
        "FROM transaksi WHERE jenis = ? ORDER BY id",
        (jenis,),
    ).fetchall()
    conn.close()
    senarai = [dict(r) for r in baris]
    jumlah_semua = round(sum(r["jumlah"] for r in senarai), 2)
    return {"jumlah_semua": jumlah_semua, "senarai": senarai}


# ---------- Laluan: umum ----------

@app.get("/")
def utama():
    fail_html = Path(__file__).parent / "index.html"
    return FileResponse(fail_html)

# ---------- Laluan: akaun ----------

@app.post("/akaun")
def tambah_akaun(a: AkaunBaru):
    conn = sambung()
    kursor = conn.execute(
        "INSERT INTO akaun (nama, jenis, baki_awal) VALUES (?, ?, ?)",
        (a.nama, a.jenis, a.baki_awal),
    )
    conn.commit()
    id_baru = kursor.lastrowid
    conn.close()
    return {"id": id_baru, "nama": a.nama, "jenis": a.jenis,
            "baki_awal": a.baki_awal}


@app.get("/akaun")
def lihat_akaun():
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
        LEFT JOIN transaksi t ON t.akaun_id = a.id
        GROUP BY a.id
        ORDER BY a.id
        """
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
def padam_akaun(id_akaun: int):
    conn = sambung()
    bilangan = conn.execute(
        "SELECT COUNT(*) AS n FROM transaksi WHERE akaun_id = ?", (id_akaun,)
    ).fetchone()["n"]
    if bilangan > 0:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Akaun ini masih ada transaksi. Padam atau pindahkan transaksi dahulu.",
        )
    kursor = conn.execute("DELETE FROM akaun WHERE id = ?", (id_akaun,))
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Akaun tidak dijumpai")
    return {"mesej": f"Akaun {id_akaun} telah dipadam"}


# ---------- Laluan: transaksi ----------

@app.post("/belanja")
def tambah_belanja(b: TransaksiBaru):
    return simpan("belanja", b)


@app.get("/belanja")
def lihat_belanja():
    return senarai_ikut_jenis("belanja")


@app.post("/pendapatan")
def tambah_pendapatan(p: TransaksiBaru):
    return simpan("pendapatan", p)


@app.get("/pendapatan")
def lihat_pendapatan():
    return senarai_ikut_jenis("pendapatan")


@app.get("/baki")
def lihat_baki():
    conn = sambung()
    baki_awal = conn.execute(
        "SELECT COALESCE(SUM(baki_awal), 0) AS n FROM akaun"
    ).fetchone()["n"]
    conn.close()
    jumlah_pendapatan = senarai_ikut_jenis("pendapatan")["jumlah_semua"]
    jumlah_belanja = senarai_ikut_jenis("belanja")["jumlah_semua"]
    return {
        "baki_awal_akaun": round(baki_awal, 2),
        "pendapatan": jumlah_pendapatan,
        "belanja": jumlah_belanja,
        "baki": round(baki_awal + jumlah_pendapatan - jumlah_belanja, 2),
    }


@app.put("/transaksi/{id_transaksi}")
def kemaskini_transaksi(id_transaksi: int, data: TransaksiKemaskini):
    conn = sambung()
    sedia_ada = conn.execute(
        "SELECT * FROM transaksi WHERE id = ?", (id_transaksi,)
    ).fetchone()
    if sedia_ada is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Transaksi tidak dijumpai")

    semak_akaun_wujud(conn, data.akaun_id)

    jumlah = data.jumlah if data.jumlah is not None else sedia_ada["jumlah"]
    kategori = data.kategori if data.kategori is not None else sedia_ada["kategori"]
    catatan = data.catatan if data.catatan is not None else sedia_ada["catatan"]
    akaun_id = data.akaun_id if data.akaun_id is not None else sedia_ada["akaun_id"]

    conn.execute(
        "UPDATE transaksi SET jumlah = ?, kategori = ?, catatan = ?, akaun_id = ? "
        "WHERE id = ?",
        (jumlah, kategori, catatan, akaun_id, id_transaksi),
    )
    conn.commit()
    baru = conn.execute(
        "SELECT * FROM transaksi WHERE id = ?", (id_transaksi,)
    ).fetchone()
    conn.close()
    return dict(baru)


@app.delete("/transaksi/{id_transaksi}")
def padam_transaksi(id_transaksi: int):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM transaksi WHERE id = ?", (id_transaksi,)
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Transaksi tidak dijumpai")
    return {"mesej": f"Transaksi {id_transaksi} telah dipadam"}

   # ---------- Bajet bulanan ----------

def sedia_bajet():
    """Cipta jadual bajet jika belum wujud."""
    conn = sambung()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bajet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kategori TEXT NOT NULL UNIQUE COLLATE NOCASE,
            had_bulanan REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


sedia_bajet()


class BajetBaru(BaseModel):
    kategori: str
    had_bulanan: float


def tentukan_status(had, belanja):
    """Kira peratus guna dan beri amaran."""
    peratus = round(belanja / had * 100, 1) if had > 0 else 0
    if peratus >= 100:
        status = "melebihi"
        amaran = "Bajet sudah melebihi had!"
    elif peratus >= 80:
        status = "hampir habis"
        amaran = "Awas, bajet hampir habis."
    else:
        status = "selamat"
        amaran = ""
    return peratus, status, amaran


@app.post("/bajet")
def tetapkan_bajet(b: BajetBaru):
    if b.had_bulanan <= 0:
        raise HTTPException(status_code=400, detail="Had mesti lebih daripada 0")
    conn = sambung()
    conn.execute(
        "INSERT INTO bajet (kategori, had_bulanan) VALUES (?, ?) "
        "ON CONFLICT(kategori) DO UPDATE SET had_bulanan = excluded.had_bulanan",
        (b.kategori, b.had_bulanan),
    )
    conn.commit()
    conn.close()
    return {"kategori": b.kategori, "had_bulanan": b.had_bulanan}


@app.get("/bajet")
def lihat_bajet(bulan: Optional[str] = None):
    # bulan dalam bentuk YYYY-MM, contoh 2026-11. Kosong = bulan ini.
    if bulan is None:
        bulan = str(date.today())[:7]

    conn = sambung()
    senarai_bajet = conn.execute(
        "SELECT kategori, had_bulanan FROM bajet ORDER BY kategori"
    ).fetchall()

    hasil = []
    for rekod in senarai_bajet:
        baris = conn.execute(
            "SELECT COALESCE(SUM(jumlah), 0) AS n FROM transaksi "
            "WHERE jenis = 'belanja' AND kategori = ? COLLATE NOCASE "
            "AND tarikh LIKE ?",
            (rekod["kategori"], bulan + "%"),
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
def padam_bajet(kategori: str):
    conn = sambung()
    kursor = conn.execute(
        "DELETE FROM bajet WHERE kategori = ? COLLATE NOCASE", (kategori,)
    )
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bajet tidak dijumpai")
    return {"mesej": f"Bajet {kategori} telah dipadam"} 

    # ---------- Tapis, cari dan ringkasan ----------

def semak_bulan(bulan: str):
    """Pastikan bulan dalam bentuk YYYY-MM."""
    sah = (
        len(bulan) == 7
        and bulan[4] == "-"
        and bulan[:4].isdigit()
        and bulan[5:].isdigit()
    )
    if sah:
        sah = 1 <= int(bulan[5:]) <= 12
    if not sah:
        raise HTTPException(
            status_code=400,
            detail="Format bulan mesti YYYY-MM, contoh 2026-11",
        )


def bulan_lepas(bulan: str):
    """Pulangkan bulan sebelumnya dalam bentuk YYYY-MM."""
    tahun = int(bulan[:4])
    bln = int(bulan[5:])
    if bln == 1:
        return f"{tahun - 1}-12"
    return f"{tahun}-{bln - 1:02d}"


def jumlah_bulan(conn, jenis: str, bulan: str):
    """Jumlah semua transaksi satu jenis dalam satu bulan."""
    baris = conn.execute(
        "SELECT COALESCE(SUM(jumlah), 0) AS n FROM transaksi "
        "WHERE jenis = ? AND tarikh LIKE ?",
        (jenis, bulan + "%"),
    ).fetchone()
    return round(baris["n"], 2)


@app.get("/transaksi")
def cari_transaksi(
    jenis: Optional[str] = None,
    kategori: Optional[str] = None,
    akaun_id: Optional[int] = None,
    dari: Optional[str] = None,
    hingga: Optional[str] = None,
    kata_kunci: Optional[str] = None,
):
    # dari dan hingga dalam bentuk YYYY-MM-DD
    syarat = []
    nilai = []

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
        "FROM transaksi"
    )
    if syarat:
        sql += " WHERE " + " AND ".join(syarat)
    sql += " ORDER BY tarikh DESC, id DESC"

    conn = sambung()
    baris = conn.execute(sql, nilai).fetchall()
    conn.close()

    senarai = [dict(r) for r in baris]
    jumlah_pendapatan = round(
        sum(r["jumlah"] for r in senarai if r["jenis"] == "pendapatan"), 2
    )
    jumlah_belanja = round(
        sum(r["jumlah"] for r in senarai if r["jenis"] == "belanja"), 2
    )
    return {
        "bilangan": len(senarai),
        "jumlah_pendapatan": jumlah_pendapatan,
        "jumlah_belanja": jumlah_belanja,
        "senarai": senarai,
    }


@app.get("/ringkasan")
def ringkasan_bulanan(bulan: Optional[str] = None):
    # bulan dalam bentuk YYYY-MM. Kosong = bulan ini.
    if bulan is None:
        bulan = str(date.today())[:7]
    semak_bulan(bulan)
    lepas = bulan_lepas(bulan)

    conn = sambung()
    pendapatan = jumlah_bulan(conn, "pendapatan", bulan)
    belanja = jumlah_bulan(conn, "belanja", bulan)
    belanja_lepas = jumlah_bulan(conn, "belanja", lepas)
    bilangan = conn.execute(
        "SELECT COUNT(*) AS n FROM transaksi WHERE tarikh LIKE ?",
        (bulan + "%",),
    ).fetchone()["n"]
    baris = conn.execute(
        "SELECT kategori, SUM(jumlah) AS jumlah FROM transaksi "
        "WHERE jenis = 'belanja' AND tarikh LIKE ? "
        "GROUP BY kategori COLLATE NOCASE ORDER BY jumlah DESC",
        (bulan + "%",),
    ).fetchall()
    conn.close()

    ikut_kategori = []
    for r in baris:
        jumlah = round(r["jumlah"], 2)
        peratus = round(jumlah / belanja * 100, 1) if belanja > 0 else 0
        ikut_kategori.append(
            {"kategori": r["kategori"], "jumlah": jumlah, "peratus": peratus}
        )

    kategori_tertinggi = None
    if ikut_kategori:
        kategori_tertinggi = ikut_kategori[0]["kategori"]

    perubahan = None
    if belanja_lepas > 0:
        perubahan = round((belanja - belanja_lepas) / belanja_lepas * 100, 1)

    return {
        "bulan": bulan,
        "pendapatan": pendapatan,
        "belanja": belanja,
        "baki_bulan_ini": round(pendapatan - belanja, 2),
        "bilangan_transaksi": bilangan,
        "kategori_tertinggi": kategori_tertinggi,
        "belanja_ikut_kategori": ikut_kategori,
        "belanja_bulan_lepas": belanja_lepas,
        "perubahan_berbanding_bulan_lepas_peratus": perubahan,
    }

      # ---------- Matlamat simpanan ----------

def sedia_matlamat():
    """Cipta jadual matlamat jika belum wujud."""
    conn = sambung()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS matlamat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            sasaran REAL NOT NULL,
            terkumpul REAL NOT NULL DEFAULT 0,
            tarikh_sasaran TEXT
        )
        """
    )
    conn.commit()
    conn.close()


sedia_matlamat()


class MatlamatBaru(BaseModel):
    nama: str
    sasaran: float
    tarikh_sasaran: Optional[str] = None  # YYYY-MM-DD


class Simpanan(BaseModel):
    jumlah: float


def butiran_matlamat(r):
    """Kira peratus, baki dan simpanan bulanan yang diperlukan."""
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
            bulan_lagi = (
                (sasaran_tarikh.year - hari_ini.year) * 12
                + (sasaran_tarikh.month - hari_ini.month)
            )
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


def ambil_matlamat(conn, id_matlamat: int):
    """Ambil satu matlamat, atau beri ralat 404."""
    r = conn.execute(
        "SELECT * FROM matlamat WHERE id = ?", (id_matlamat,)
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Matlamat tidak dijumpai")
    return r


@app.post("/matlamat")
def tambah_matlamat(m: MatlamatBaru):
    if m.sasaran <= 0:
        raise HTTPException(status_code=400, detail="Sasaran mesti lebih daripada 0")


@app.get("/matlamat")
def lihat_matlamat():
    conn = sambung()
    baris = conn.execute("SELECT * FROM matlamat ORDER BY id").fetchall()
    conn.close()
    senarai = [butiran_matlamat(r) for r in baris]
    return {"bilangan": len(senarai), "senarai": senarai}


@app.post("/matlamat/{id_matlamat}/simpan")
def tambah_simpanan(id_matlamat: int, s: Simpanan):
    if s.jumlah <= 0:
        raise HTTPException(status_code=400, detail="Jumlah simpanan mesti lebih daripada 0")
    conn = sambung()
    ambil_matlamat(conn, id_matlamat)
    conn.execute(
        "UPDATE matlamat SET terkumpul = round(terkumpul + ?, 2) WHERE id = ?",
        (s.jumlah, id_matlamat),
    )
    conn.commit()
    r = ambil_matlamat(conn, id_matlamat)
    conn.close()
    return butiran_matlamat(r)


@app.delete("/matlamat/{id_matlamat}")
def padam_matlamat(id_matlamat: int):
    conn = sambung()
    kursor = conn.execute("DELETE FROM matlamat WHERE id = ?", (id_matlamat,))
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Matlamat tidak dijumpai")
    return {"mesej": f"Matlamat {id_matlamat} telah dipadam"}
    """Ambil satu matlamat, atau beri ralat 404."""
    r = conn.execute(
        "SELECT * FROM matlamat WHERE id = ?", (id_matlamat,)
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Matlamat tidak dijumpai")
    return r

    # ---------- Belanja berulang ----------

def sedia_berulang():
    """Cipta jadual belanja berulang jika belum wujud."""
    conn = sambung()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS berulang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    conn.commit()
    conn.close()


sedia_berulang()


class BerulangBaru(BaseModel):
    jenis: Literal["belanja", "pendapatan"]
    jumlah: float
    kategori: str
    catatan: str = ""
    akaun_id: Optional[int] = None
    hari: int  # hari dalam bulan, 1 hingga 28


def ambil_berulang(conn, id_berulang: int):
    """Ambil satu rekod berulang, atau beri ralat 404."""
    r = conn.execute(
        "SELECT * FROM berulang WHERE id = ?", (id_berulang,)
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
def tambah_berulang(b: BerulangBaru):
    if b.jumlah <= 0:
        raise HTTPException(status_code=400, detail="Jumlah mesti lebih daripada 0")
    if b.hari < 1 or b.hari > 28:
        raise HTTPException(
            status_code=400,
            detail="Hari mesti antara 1 hingga 28 (supaya sesuai untuk semua bulan)",
        )
    conn = sambung()
    semak_akaun_wujud(conn, b.akaun_id)
    kursor = conn.execute(
        "INSERT INTO berulang (jenis, jumlah, kategori, catatan, akaun_id, hari) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (b.jenis, b.jumlah, b.kategori, b.catatan, b.akaun_id, b.hari),
    )
    conn.commit()
    r = ambil_berulang(conn, kursor.lastrowid)
    conn.close()
    return huraian_berulang(r)

    # ---------- To-Do List & Peringatan ----------

def sedia_tugasan():
    """Cipta jadual tugasan jika belum wujud."""
    conn = sambung()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tugasan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tajuk TEXT NOT NULL,
            catatan TEXT DEFAULT '',
            masa_peringatan TEXT,
            selesai INTEGER NOT NULL DEFAULT 0,
            sudah_beritahu INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


sedia_tugasan()


class TugasanBaru(BaseModel):
    tajuk: str
    catatan: str = ""
    masa_peringatan: Optional[str] = None  # Format: YYYY-MM-DD HH:MM


def semak_format_masa(masa: str):
    """Pastikan format masa ialah YYYY-MM-DD HH:MM."""
    try:
        datetime.strptime(masa, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Format masa_peringatan mesti YYYY-MM-DD HH:MM, contoh 2026-11-21 18:30",
        )


def huraian_tugasan(r):
    return {
        "id": r["id"],
        "tajuk": r["tajuk"],
        "catatan": r["catatan"],
        "masa_peringatan": r["masa_peringatan"],
        "selesai": bool(r["selesai"]),
        "sudah_beritahu": bool(r["sudah_beritahu"]),
    }


@app.post("/tugasan")
def tambah_tugasan(t: TugasanBaru):
    if not t.tajuk.strip():
        raise HTTPException(status_code=400, detail="Tajuk tugasan tidak boleh kosong")
    if t.masa_peringatan:
        semak_format_masa(t.masa_peringatan)

    conn = sambung()
    kursor = conn.execute(
        "INSERT INTO tugasan (tajuk, catatan, masa_peringatan) VALUES (?, ?, ?)",
        (t.tajuk.strip(), t.catatan, t.masa_peringatan),
    )
    conn.commit()
    r = conn.execute(
        "SELECT * FROM tugasan WHERE id = ?", (kursor.lastrowid,)
    ).fetchone()
    conn.close()
    return huraian_tugasan(r)


@app.get("/tugasan")
def lihat_tugasan(selesai: Optional[bool] = None):
    conn = sambung()
    if selesai is None:
        baris = conn.execute(
            "SELECT * FROM tugasan ORDER BY selesai ASC, masa_peringatan ASC, id DESC"
        ).fetchall()
    else:
        baris = conn.execute(
            "SELECT * FROM tugasan WHERE selesai = ? ORDER BY masa_peringatan ASC, id DESC",
            (1 if selesai else 0,),
        ).fetchall()
    conn.close()
    senarai = [huraian_tugasan(r) for r in baris]
    return {"bilangan": len(senarai), "senarai": senarai}


@app.get("/tugasan/peringatan")
def semak_peringatan():
    """Semak tugasan yang sudah sampai masanya dan tandakan sebagai sudah diberitahu."""
    sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sambung()
    baris = conn.execute(
        """
        SELECT * FROM tugasan
        WHERE selesai = 0
          AND sudah_beritahu = 0
          AND masa_peringatan IS NOT NULL
          AND masa_peringatan <= ?
        ORDER BY masa_peringatan ASC
        """,
        (sekarang,),
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
def togol_tugasan_selesai(id_tugasan: int):
    conn = sambung()
    r = conn.execute(
        "SELECT * FROM tugasan WHERE id = ?", (id_tugasan,)
    ).fetchone()
    if r is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Tugasan tidak dijumpai")

    status_baru = 0 if r["selesai"] else 1
    conn.execute(
        "UPDATE tugasan SET selesai = ? WHERE id = ?",
        (status_baru, id_tugasan),
    )
    conn.commit()
    r_baru = conn.execute(
        "SELECT * FROM tugasan WHERE id = ?", (id_tugasan,)
    ).fetchone()
    conn.close()
    return huraian_tugasan(r_baru)


@app.delete("/tugasan/{id_tugasan}")
def padam_tugasan(id_tugasan: int):
    conn = sambung()
    kursor = conn.execute("DELETE FROM tugasan WHERE id = ?", (id_tugasan,))
    conn.commit()
    conn.close()
    if kursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Tugasan tidak dijumpai")
    return {"mesej": f"Tugasan {id_tugasan} telah dipadam"}