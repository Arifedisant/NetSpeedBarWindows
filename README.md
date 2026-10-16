# Net Speed Bar untuk Windows 11

Widget kecil di **taskbar Windows 11** yang menunjukkan **kecepatan upload dan download internet**
secara langsung, seperti net speed meter di HP Android.

```
↑  9.9 KB/s     <- upload (oranye)
↓  2.1 KB/s     <- download (hijau)
```

Ditulis dengan Python (psutil + Pillow + tkinter), satu file, tanpa installer. Pengganti ringan untuk
TrafficMonitor.

Saudaranya: [Claude Code Usage Bar untuk Windows](https://github.com/Arifedisant/ClaudeCodeUsageBarWindows),
sisa jatah Claude Code. Kalau keduanya dipasang, Net Speed Bar otomatis menempel di sebelah kanannya.

## Fitur

- Kecepatan upload dan download, diperbarui tiap detik.
- **Klik** = panel detail: grafik 60 detik terakhir, kecepatan puncak, total data terpakai hari ini.
- **Seret** = pindah posisi (tersimpan). **Klik kanan** = satuan, transparansi, kembalikan ke posisi awal,
  jalan saat Windows menyala, keluar.
- Satuan bisa dipilih: **bit/s** (Kb/s, Mb/s, seperti iklan ISP) atau **byte/s** (KB/s, MB/s, seperti kecepatan download).
- Hanya kartu jaringan fisik yang dihitung. ZeroTier, vEthernet/Hyper-V, VPN, loopback dan sejenisnya dilewati
  supaya trafik tidak terhitung dua kali (daftar di `SKIP_WORDS`).
- Latar mengikuti warna taskbar, jadi tampak transparan tapi tetap bisa diklik dan diseret.
- Menyesuaikan diri dengan skala layar (DPI). Hanya satu salinan yang bisa jalan sekaligus.

## Kebutuhan

- Windows 11 (Windows 10 kemungkinan jalan, tapi tidak diuji)
- [Python 3.13](https://www.python.org/downloads/) (centang *Add python.exe to PATH* saat memasang)

## Pasang

1. Unduh repo ini: tombol hijau **Code → Download ZIP**, lalu ekstrak (atau `git clone`).
2. Taruh foldernya di tempat tetap, misalnya `%USERPROFILE%\NetSpeedBar`.
   Jangan di folder Downloads yang suka dibersihkan, karena autostart menunjuk ke lokasi ini.
3. Buka PowerShell di folder itu dan jalankan:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   Skrip itu memasang `psutil` dan `Pillow` lewat pip, mendaftarkan widget supaya jalan setiap Windows
   menyala (`HKCU\...\CurrentVersion\Run`, nilai `NetSpeedBar`), lalu langsung menyalakannya.

## File

| File | Isi |
| --- | --- |
| `netspeedbar.pyw` | seluruh widget |
| `setup.ps1` | pemasang untuk komputer baru |
| `%LOCALAPPDATA%\NetSpeedBar\config.json` | posisi, satuan, transparansi, total harian (dibuat otomatis) |

## Kalau ada yang aneh

Jalankan `python .\netspeedbar.pyw` (bukan `pythonw`) supaya error-nya kelihatan di konsol.
Widget tidak muncul setelah restart: cek nilai `NetSpeedBar` di `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

## Copot

Klik kanan → **Keluar**, hapus centang **Jalan saat Windows menyala** (atau hapus nilai `NetSpeedBar`
di key `Run`), lalu hapus foldernya dan `%LOCALAPPDATA%\NetSpeedBar`.
