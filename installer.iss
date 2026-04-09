; installer.iss — Script Inno Setup pour OpenCleaner
; Télécharger Inno Setup : https://jrsoftware.org/isdl.php

#define AppName      "OpenCleaner"
#define AppVersion   "1.0.0"
#define AppPublisher "OpenCleaner"
#define AppURL       "https://github.com/TON_USERNAME/opencleaner"
#define AppExeName   "OpenCleaner.exe"
#define DistDir      "dist\OpenCleaner"

[Setup]
AppId={{A3F2B8C1-4D7E-4F9A-B0C2-1E3D5F7A9B2C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
; Pas d'élévation forcée à l'install — l'app gère elle-même
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=OpenCleanerSetup-v{#AppVersion}
; SignTool=signtool         ; décommenter pour la signature de code
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
; UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} — Nettoyeur PC open source
VersionInfoCopyright=MIT License

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon";    Description: "Créer un raccourci sur le Bureau";     GroupDescription: "Raccourcis :"; Flags: unchecked
Name: "quicklaunchicon"; Description: "Épingler dans la barre des tâches";   GroupDescription: "Raccourcis :"; Flags: unchecked

[Files]
; Tout le dossier dist généré par PyInstaller
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Menu Démarrer
Name: "{group}\{#AppName}";            Filename: "{app}\{#AppExeName}"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
; Bureau (optionnel)
Name: "{autodesktop}\{#AppName}";      Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Proposer de lancer l'app à la fin de l'installation
Filename: "{app}\{#AppExeName}"; \
  Description: "Lancer {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nettoie les fichiers générés à l'exécution
Type: files;     Name: "{app}\history.json"
Type: files;     Name: "{app}\schedule.json"
Type: dirifempty; Name: "{app}"
