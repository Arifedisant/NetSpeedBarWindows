# Net Speed Bar

Kecepatan upload/download di taskbar Windows 11, satu gaya dengan
[AI Usage Bar](https://github.com/Arifedisant/AIUsageBar). Ditulis dengan Python + psutil + Pillow + tkinter.

## Pasang

1. Pasang **Python 3.13** dari python.org (centang *Add python.exe to PATH*).
2. Unduh repo ini (**Code → Download ZIP**, atau `git clone`) dan taruh foldernya di tempat tetap,
   misalnya `%USERPROFILE%\NetSpeedBar`.
3. Dari folder itu jalankan:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   Skrip itu memasang `psutil` dan `Pillow`, mendaftarkan autostart, lalu menyalakan widgetnya.

## Cara pakai

- Klik = panel detail (grafik 60 detik, puncak, total hari ini). Seret = pindah. Klik kanan = satuan bit/byte, transparansi, autostart, keluar.
- Posisi awal tepat di kanan AI Usage Bar kalau widget itu ada; posisi hasil seret disimpan.
- Config + total harian: `%LOCALAPPDATA%\NetSpeedBar\config.json`
- Autostart: `HKCU\...\CurrentVersion\Run` value `NetSpeedBar` (bisa dimatikan dari klik kanan).
- Hanya adapter fisik yang dihitung; ZeroTier, vEthernet, VPN, loopback dll. dikecualikan (`SKIP_WORDS`) supaya trafik tidak terhitung dua kali.
- Hanya satu instance (mutex `Local\NetSpeedBar-single`).
