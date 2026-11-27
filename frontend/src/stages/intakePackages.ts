export const extensions = ['.pdf', '.xml', '.txt', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.zip'];

export interface IntakePackage {
  label: string;
  files: File[];
  skipped: number;
  problem?: string;
}

export function claimantPackage(label: string, selection: File[]): IntakePackage {
  const files = selection.filter(file => extensions.some(ext => file.name.toLowerCase().endsWith(ext)));
  const problem = !files.length ? 'No supported documents found.'
    : new Set(files.map(file => file.name)).size !== files.length ? 'Two documents in this claimant’s package have the same filename. Rename one before importing.'
    : files.filter(file => file.name.toLowerCase().endsWith('.xml')).length > 1 ? 'More than one request XML found. Separate the claimant requests before uploading.' : undefined;
  return { label, files, skipped: selection.length - files.length, problem };
}

export function selectedPackages(files: File[]): IntakePackage[] {
  const archives = files.filter(file => file.name.toLowerCase().endsWith('.zip'));
  if (archives.length && archives.length !== files.length) throw new Error('Choose either loose documents for one claimant or separate claimant ZIPs. Do not mix them.');
  return archives.length ? archives.map(file => claimantPackage(file.name, [file])) : [claimantPackage('Selected documents', files)];
}

type FolderItem = IntakeFolder | { name: string; read: () => Promise<File> };
export type IntakeFolder = { name: string; children: () => Promise<FolderItem[]> };
export type PickerDirectory = { kind: 'directory'; name: string; values: () => AsyncIterable<PickerDirectory | FileSystemFileHandle> };

function visible(name: string) { return !name.startsWith('.') && name !== '__MACOSX'; }
function isFolder(item: FolderItem): item is IntakeFolder { return 'children' in item; }

export function fromDirectoryHandle(handle: PickerDirectory): IntakeFolder {
  return {name: handle.name, children: async () => {
    const items: FolderItem[] = [];
    for await (const child of handle.values()) {
      if (visible(child.name)) items.push(child.kind === 'directory' ? fromDirectoryHandle(child) : {name: child.name, read: () => child.getFile()});
    }
    return items;
  }};
}

export function fromDirectoryInput(files: File[]): IntakeFolder {
  const roots = new Set<string>();
  const tree = new Map<string, FolderItem[]>();
  for (const file of files) {
    const path = (file.webkitRelativePath || '').split('/');
    if (path.length < 2 || path.some(part => !part || part === '.' || part === '..')) throw new Error('Folder paths are unavailable. Drop the claimant folders or choose one ZIP per claimant instead.');
    roots.add(path[0]);
    if (!path.slice(1).every(visible)) continue;
    let key = path[0];
    if (!tree.has(key)) tree.set(key, []);
    for (const name of path.slice(1, -1)) {
      const childKey = `${key}/${name}`;
      if (!tree.has(childKey)) {
        tree.set(childKey, []);
        tree.get(key)!.push({name, children: async () => tree.get(childKey)!});
      }
      key = childKey;
    }
    tree.get(key)!.push({name: file.name, read: async () => file});
  }
  if (roots.size !== 1) throw new Error('Choose one parent folder, then select the claimant folders in Intake.');
  const [name] = roots;
  return {name, children: async () => tree.get(name) ?? []};
}

export function fromDroppedDirectory(entry: FileSystemDirectoryEntry): IntakeFolder {
  return {name: entry.name, children: async () => {
    const reader = entry.createReader();
    const items: FolderItem[] = [];
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) return items;
      for (const child of batch.filter(child => visible(child.name))) {
        items.push(child.isDirectory ? fromDroppedDirectory(child as FileSystemDirectoryEntry) : {
          name: child.name, read: () => new Promise<File>((resolve, reject) => (child as FileSystemFileEntry).file(resolve, reject)),
        });
      }
    }
  }};
}

export async function listClaimantFolders(parent: IntakeFolder) {
  const children = await parent.children();
  const folders = children.filter(isFolder).sort((left, right) => left.name.localeCompare(right.name, undefined, {numeric: true}));
  return {name: parent.name, folders, rootFiles: children.length - folders.length};
}

export async function readClaimantFolder(folder: IntakeFolder): Promise<IntakePackage> {
  const files: File[] = [];
  async function collect(directory: IntakeFolder) {
    for (const child of await directory.children()) {
      if (isFolder(child)) await collect(child); else files.push(await child.read());
    }
  }
  try { await collect(folder); return claimantPackage(folder.name, files); }
  catch { return {label: folder.name, files: [], skipped: 0, problem: 'This folder could not be read. Re-select it or use a claimant ZIP.'}; }
}

export async function droppedPackages(entries: FileSystemEntry[], loose: File[], singleClaimant: boolean): Promise<IntakePackage[]> {
  const folders = entries.filter(entry => entry.isDirectory);
  if (!folders.length) {
    if (!loose.length || loose.some(file => !file.name.includes('.') && !file.size)) throw new Error('Folder contents are unavailable. Choose one ZIP per claimant instead.');
    return selectedPackages(loose);
  }
  if (folders.length !== entries.length || loose.length) throw new Error('Drop claimant folders without loose files so records are not assigned to the wrong case.');
  if (singleClaimant && folders.length !== 1) throw new Error('Choose only one claimant’s folder in this layout.');
  return Promise.all(folders.map(folder => readClaimantFolder(fromDroppedDirectory(folder as FileSystemDirectoryEntry))));
}
