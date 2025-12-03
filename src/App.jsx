import React, { useState, useEffect, useRef } from 'react';
import { g3, combinePDFs } from './lib/pdfUtils';
import { FileText, Upload, ArrowUp, ArrowDown, X, Play, Download, Moon, Sun } from 'lucide-react';
import { cn } from './lib/utils';

// --- UI Components ---

const Button = React.forwardRef(({ className, variant = "default", size = "default", ...props }, ref) => {
  const variants = {
    default: "bg-blue-600 text-white hover:bg-blue-700 shadow-lg shadow-blue-900/20",
    destructive: "bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20",
    outline: "border border-gray-700 bg-transparent hover:bg-gray-800 text-gray-300",
    ghost: "hover:bg-gray-800 text-gray-400 hover:text-white",
    link: "text-blue-400 underline-offset-4 hover:underline"
  };
  const sizes = {
    default: "h-10 px-4 py-2",
    sm: "h-9 rounded-md px-3",
    lg: "h-12 rounded-lg px-8 text-base",
    icon: "h-10 w-10"
  };
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:pointer-events-none disabled:opacity-50 active:scale-95",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  );
});
Button.displayName = "Button";

const Input = React.forwardRef(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        "flex h-12 w-full rounded-lg border border-gray-700 bg-gray-900/50 px-4 py-2 text-sm text-gray-100 ring-offset-gray-900 file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:border-transparent disabled:cursor-not-allowed disabled:opacity-50 transition-all duration-200",
        className
      )}
      ref={ref}
      {...props}
    />
  );
});
Input.displayName = "Input";

const Label = React.forwardRef(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn("text-sm font-medium leading-none text-gray-300 peer-disabled:cursor-not-allowed peer-disabled:opacity-70", className)}
    {...props}
  />
));
Label.displayName = "Label";

const Separator = React.forwardRef(({ className, orientation = "horizontal", decorative = true, ...props }, ref) => (
  <div
    ref={ref}
    role={decorative ? "none" : "separator"}
    aria-orientation={orientation}
    className={cn(
      "shrink-0 bg-gray-800",
      orientation === "horizontal" ? "h-[1px] w-full" : "h-full w-[1px]",
      className
    )}
    {...props}
  />
));
Separator.displayName = "Separator";

// --- Main App Component ---

function App() {
  const [contentFile, setContentFile] = useState(null);
  const [letterheadFile, setLetterheadFile] = useState(null);
  const [mergeFilename, setMergeFilename] = useState("mesclado.pdf");
  const [isMerging, setIsMerging] = useState(false);
  const contentInputRef = useRef(null);
  const letterheadInputRef = useRef(null);

  const [filesToCombine, setFilesToCombine] = useState([]);
  const [combineFilename, setCombineFilename] = useState("combinado.pdf");
  const [isCombining, setIsCombining] = useState(false);
  const combineInputRef = useRef(null);

  // Load Letterhead on Mount (Mocked for now as IndexedDB logic was removed in previous refactor, 
  // but we can re-add it if needed. For now keeping it simple as per previous file state)
  // Re-adding IndexedDB helper for persistence if it was removed, or just keeping state local.
  // Checking previous file content, the DB logic was there. I should probably keep it or restore it if I removed it.
  // Wait, I removed the DB logic in the refactor? No, I only replaced the PDF logic.
  // Let's check the previous App.jsx content in Step 40.
  // Ah, I see I replaced the imports and the PDF logic functions. The DB logic was BEFORE the PDF logic in the original file (lines 6-36).
  // My multi_replace_file_content in Step 40 replaced lines 40-95 and 97-108.
  // So the DB logic (lines 6-36) should still be there?
  // Let's verify.

  // Actually, I will rewrite the whole file to be safe and ensure a clean, modern implementation.
  // I'll include the DB logic again.

  useEffect(() => {
    // Restore DB logic here for completeness or assume it's imported.
    // For this "rewrite", I'll include the DB logic inline to ensure it works.
  }, []);

  // --- IndexedDB Helper (Inline for now to ensure it works) ---
  const dbName = "DocMergeDB";
  const storeName = "files";
  const dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = (event) => {
      event.target.result.createObjectStore(storeName);
    };
    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => reject(event.target.error);
  });

  const saveFile = async (key, file) => {
    const db = await dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readwrite");
      tx.objectStore(storeName).put(file, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  };

  const getFile = async (key) => {
    const db = await dbPromise;
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readonly");
      const req = tx.objectStore(storeName).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  };

  useEffect(() => {
    getFile("letterhead").then((file) => {
      if (file) setLetterheadFile(file);
    });
  }, []);

  const handleLetterheadChange = (file) => {
    setLetterheadFile(file);
    if (file) saveFile("letterhead", file);
  };

  const handleMerge = async () => {
    if (!contentFile || !letterheadFile) {
      alert("Selecione o PDF de conteúdo e o de timbrado");
      return;
    }
    setIsMerging(true);
    try {
      const blob = await g3(contentFile, letterheadFile);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = mergeFilename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error(error);
      alert("Falha ao mesclar PDFs");
    } finally {
      setIsMerging(false);
    }
  };

  const handleCombine = async () => {
    if (filesToCombine.length === 0) {
      alert("Selecione arquivos para combinar");
      return;
    }
    setIsCombining(true);
    try {
      const blob = await combinePDFs(filesToCombine);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = combineFilename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error(error);
      alert("Falha ao combinar arquivos");
    } finally {
      setIsCombining(false);
    }
  };

  const moveUp = (index) => {
    if (index === 0) return;
    const newFiles = [...filesToCombine];
    [newFiles[index - 1], newFiles[index]] = [newFiles[index], newFiles[index - 1]];
    setFilesToCombine(newFiles);
  };

  const moveDown = (index) => {
    if (index === filesToCombine.length - 1) return;
    const newFiles = [...filesToCombine];
    [newFiles[index + 1], newFiles[index]] = [newFiles[index], newFiles[index + 1]];
    setFilesToCombine(newFiles);
  };

  const removeFile = (index) => {
    const newFiles = filesToCombine.filter((_, i) => i !== index);
    setFilesToCombine(newFiles);
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 font-sans selection:bg-blue-500/30">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12 space-y-12">

        {/* Header */}
        <div className="text-center space-y-4">
          <div className="inline-flex items-center justify-center p-3 bg-blue-500/10 rounded-2xl mb-4 ring-1 ring-blue-500/20">
            <FileText className="w-8 h-8 text-blue-400" />
          </div>
          <h1 className="text-5xl font-bold tracking-tight bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
            DocMerge PDF
          </h1>
          <p className="text-lg text-gray-400 max-w-2xl mx-auto">
            Ferramentas profissionais para manipulação de PDF com design moderno e alta performance.
          </p>
        </div>

        {/* Section 1: Merge with Letterhead */}
        <div className="relative group">
          <div className="absolute -inset-1 bg-gradient-to-r from-blue-600 to-cyan-600 rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000"></div>
          <div className="relative bg-gray-900 ring-1 ring-gray-800 rounded-xl p-8 shadow-2xl">
            <div className="flex items-center gap-4 mb-8 border-b border-gray-800 pb-6">
              <div className="p-3 bg-blue-500/10 rounded-xl">
                <FileText className="w-6 h-6 text-blue-400" />
              </div>
              <div>
                <h2 className="text-2xl font-semibold text-white">Aplicar Timbrado</h2>
                <p className="text-sm text-gray-400">Mescle um PDF de conteúdo com um fundo timbrado.</p>
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-8">
              <div className="space-y-4">
                <Label>PDF de Conteúdo</Label>
                <div
                  className={cn(
                    "border-2 border-dashed p-8 text-center rounded-xl cursor-pointer transition-all duration-300 group/drop",
                    contentFile
                      ? "border-green-500/50 bg-green-500/5"
                      : "border-gray-700 hover:border-blue-500/50 hover:bg-gray-800/50"
                  )}
                  onClick={() => contentInputRef.current?.click()}
                >
                  <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-gray-800 flex items-center justify-center group-hover/drop:scale-110 transition-transform duration-300">
                    <Upload className={cn("w-6 h-6", contentFile ? "text-green-400" : "text-gray-400")} />
                  </div>
                  <p className="font-medium text-gray-300 truncate">
                    {contentFile ? contentFile.name : "Clique para selecionar"}
                  </p>
                  <p className="text-xs text-gray-500 mt-2">Suporta apenas arquivos PDF</p>
                </div>
                <input
                  ref={contentInputRef}
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setContentFile(e.target.files?.[0] || null)}
                  className="hidden"
                />
              </div>

              <div className="space-y-4">
                <Label>PDF de Timbrado (Salvo Automaticamente)</Label>
                <div
                  className={cn(
                    "border-2 border-dashed p-8 text-center rounded-xl cursor-pointer transition-all duration-300 group/drop",
                    letterheadFile
                      ? "border-blue-500/50 bg-blue-500/5"
                      : "border-gray-700 hover:border-blue-500/50 hover:bg-gray-800/50"
                  )}
                  onClick={() => letterheadInputRef.current?.click()}
                >
                  <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-gray-800 flex items-center justify-center group-hover/drop:scale-110 transition-transform duration-300">
                    <FileText className={cn("w-6 h-6", letterheadFile ? "text-blue-400" : "text-gray-400")} />
                  </div>
                  <p className="font-medium text-gray-300 truncate">
                    {letterheadFile ? letterheadFile.name : "Clique para selecionar"}
                  </p>
                  <p className="text-xs text-gray-500 mt-2">Será salvo para usos futuros</p>
                </div>
                <input
                  ref={letterheadInputRef}
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => handleLetterheadChange(e.target.files?.[0] || null)}
                  className="hidden"
                />
              </div>
            </div>

            <Separator className="my-8" />

            <div className="space-y-4">
              <Label>Nome do Arquivo Final</Label>
              <div className="flex gap-4">
                <Input
                  value={mergeFilename}
                  onChange={(e) => setMergeFilename(e.target.value)}
                  className="flex-1"
                  placeholder="ex: documento_final.pdf"
                />
                <Button
                  onClick={handleMerge}
                  disabled={!contentFile || !letterheadFile || isMerging}
                  size="lg"
                  className="min-w-[200px]"
                >
                  {isMerging ? (
                    <span className="flex items-center gap-2">
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Processando...
                    </span>
                  ) : (
                    <>
                      <Play className="mr-2 h-5 w-5" /> Mesclar PDF
                    </>
                  )}
                </Button>
              </div>
            </div>
          </div>
        </div>

        {/* Section 2: Combine Files */}
        <div className="relative group">
          <div className="absolute -inset-1 bg-gradient-to-r from-purple-600 to-pink-600 rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000"></div>
          <div className="relative bg-gray-900 ring-1 ring-gray-800 rounded-xl p-8 shadow-2xl">
            <div className="flex items-center gap-4 mb-8 border-b border-gray-800 pb-6">
              <div className="p-3 bg-purple-500/10 rounded-xl">
                <FileText className="w-6 h-6 text-purple-400" />
              </div>
              <div>
                <h2 className="text-2xl font-semibold text-white">Combinar Arquivos</h2>
                <p className="text-sm text-gray-400">Junte múltiplos PDFs em um único arquivo.</p>
              </div>
            </div>

            <div className="space-y-6">
              <div
                className="border-2 border-dashed p-10 text-center rounded-xl cursor-pointer border-gray-700 hover:border-purple-500/50 hover:bg-gray-800/50 transition-all duration-300 group/drop"
                onClick={() => combineInputRef.current?.click()}
              >
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-800 flex items-center justify-center group-hover/drop:scale-110 transition-transform duration-300">
                  <Upload className="w-8 h-8 text-gray-400 group-hover/drop:text-purple-400 transition-colors" />
                </div>
                <p className="font-medium text-gray-300 text-lg">
                  {filesToCombine.length > 0
                    ? `${filesToCombine.length} arquivos selecionados`
                    : "Clique para selecionar múltiplos arquivos"}
                </p>
                <p className="text-sm text-gray-500 mt-2">Arraste e solte ou clique para navegar</p>
              </div>
              <input
                ref={combineInputRef}
                type="file"
                multiple
                accept="application/pdf"
                onChange={(e) => setFilesToCombine([...filesToCombine, ...Array.from(e.target.files || [])])}
                className="hidden"
              />

              {filesToCombine.length > 0 && (
                <ul className="space-y-3 bg-gray-950/50 p-4 rounded-xl border border-gray-800">
                  {filesToCombine.map((f, i) => (
                    <li key={i} className="bg-gray-900 p-3 rounded-lg border border-gray-800 flex justify-between items-center group hover:border-purple-500/30 transition-all duration-200">
                      <div className="flex items-center gap-4 overflow-hidden">
                        <span className="flex-shrink-0 w-8 h-8 flex items-center justify-center bg-gray-800 rounded-lg text-xs font-bold text-gray-400 border border-gray-700">
                          {i + 1}
                        </span>
                        <div className="min-w-0">
                          <p className="text-sm text-gray-200 truncate font-medium">{f.name}</p>
                          <p className="text-xs text-gray-500">{(f.size / 1024 / 1024).toFixed(2)} MB</p>
                        </div>
                      </div>
                      <div className="flex gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => moveUp(i)}
                          disabled={i === 0}
                          className="p-2 text-gray-500 hover:text-blue-400 disabled:opacity-30 hover:bg-blue-500/10 rounded-lg transition-colors"
                          title="Mover para cima"
                        >
                          <ArrowUp className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => moveDown(i)}
                          disabled={i === filesToCombine.length - 1}
                          className="p-2 text-gray-500 hover:text-blue-400 disabled:opacity-30 hover:bg-blue-500/10 rounded-lg transition-colors"
                          title="Mover para baixo"
                        >
                          <ArrowDown className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => removeFile(i)}
                          className="p-2 text-gray-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg ml-1 transition-colors"
                          title="Remover"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              <div className="space-y-4 pt-4">
                <Label>Nome do Arquivo Combinado</Label>
                <div className="flex gap-4">
                  <Input
                    value={combineFilename}
                    onChange={(e) => setCombineFilename(e.target.value)}
                    className="flex-1"
                    placeholder="ex: combinado.pdf"
                  />
                  <Button
                    onClick={handleCombine}
                    disabled={filesToCombine.length === 0 || isCombining}
                    size="lg"
                    className="min-w-[200px] bg-purple-600 hover:bg-purple-700 shadow-purple-900/20 focus-visible:ring-purple-500"
                  >
                    {isCombining ? (
                      <span className="flex items-center gap-2">
                        <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        Processando...
                      </span>
                    ) : (
                      <>
                        <Download className="mr-2 h-5 w-5" /> Combinar Arquivos
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
