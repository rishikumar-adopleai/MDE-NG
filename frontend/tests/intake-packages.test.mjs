import assert from 'node:assert/strict';
import test from 'node:test';
import { droppedPackages, fromDirectoryHandle, fromDirectoryInput, fromDroppedDirectory, listClaimantFolders, readClaimantFolder, selectedPackages } from '../src/stages/intakePackages.ts';

function document(name, content) {
  return {name, isFile: true, file(resolve) { resolve(new File([content], name)); }};
}

function folder(name, ...children) {
  return {name, isDirectory: true, createReader() {
    const pages = children.map(child => [child]);
    return {readEntries(resolve) { resolve(pages.shift() ?? []); }};
  }};
}

test('each claimant folder keeps its own same-named documents, across reader pages and nested directories', async () => {
  const caseFolder = name => folder(name, document('esr.xml', name), folder('nested', document('scan.txt', name)));
  const roots = [caseFolder('A'), caseFolder('B')];
  const parent = await listClaimantFolders(fromDroppedDirectory(folder('Batch', ...roots)));
  for (const groups of [await droppedPackages(roots, [], false), await Promise.all(parent.folders.map(readClaimantFolder))]) {
    assert.deepEqual(groups.map(group => group.label), ['A', 'B']);
    for (const group of groups) {
      assert.equal(group.problem, undefined);
      assert.deepEqual(await Promise.all(group.files.map(file => file.text())), [group.label, group.label]);
    }
  }
});

test('unreadable, empty and ambiguous folders cannot discard the other claimants', async () => {
  const broken = {name:'Unreadable', isDirectory:true, createReader:() => ({readEntries:(_ok, fail) => fail(new Error())})};
  const groups = await droppedPackages([broken, folder('Empty'), folder('Mixed', document('a.xml', 'a'), document('b.xml', 'b')), folder('Good', document('record.txt','ok'))], [], false);
  assert.deepEqual(groups.map(group => Boolean(group.problem)), [true, true, true, false]);
  await assert.rejects(droppedPackages([folder('A'), folder('B')], [], true), /one claimant/);
  await assert.rejects(droppedPackages([folder('A')], [new File(['a'],'loose.txt')], false), /without loose files/);
});

function directoryFile(path, contents = path) {
  const file = new File([contents], path.split('/').at(-1));
  Object.defineProperty(file, 'webkitRelativePath', {value: path});
  return file;
}

test('directory input lists immediate claimants and keeps only the selected folders and their nested evidence', async () => {
  const root = fromDirectoryInput(['Batch/case_15/evidence/history.pdf', 'Batch/case_13/evidence/history.pdf', 'Batch/case_14/esr.xml', 'Batch/README.txt', 'Batch/.cache/ignore.txt', 'Batch/__MACOSX/no.pdf'].map(path => directoryFile(path)));
  const {folders, rootFiles} = await listClaimantFolders(root);
  assert.equal(rootFiles, 1);
  assert.deepEqual(folders.map(folder => folder.name), ['case_13', 'case_14', 'case_15']);
  const groups = await Promise.all([folders[0], folders[2]].map(readClaimantFolder));
  for (const group of groups) {
    assert.equal(group.problem, undefined);
    assert.equal(group.files.length, 1);
    assert.equal(await group.files[0].text(), `Batch/${group.label}/evidence/history.pdf`);
  }
});

test('one claimant folder can contain nested files; missing or mixed directory roots do not silently merge', async () => {
  const group = await readClaimantFolder(fromDirectoryInput(['Only/nested/esr.xml','Only/evidence/scan.pdf'].map(path => directoryFile(path))));
  assert.deepEqual(group.files.map(file => file.name), ['esr.xml', 'scan.pdf']);
  assert.throws(() => fromDirectoryInput([new File(['x'], 'scan.pdf')]), /Folder paths/);
  assert.throws(() => fromDirectoryInput([directoryFile('A/esr.xml'), directoryFile('B/esr.xml')]), /one parent/);
});

test('native folder browsing does not read unselected claimants, root notes, or hidden files', async () => {
  const reads = [];
  const handle = (name, ...children) => ({name, kind:'directory', async *values() { reads.push(name); yield* children; }});
  const file = name => ({name, kind:'file', async getFile() { reads.push(name); return new File(['synthetic'], name); }});
  const parent = fromDirectoryHandle(handle('Batch', file('README.txt'), handle('case_15', file('unused.pdf')), handle('case_13', handle('Evidence', file('doc.pdf'), file('.ignored.txt'))), handle('Empty')));
  const {folders, rootFiles} = await listClaimantFolders(parent);
  assert.deepEqual(reads, ['Batch']); assert.equal(rootFiles, 1);
  assert.deepEqual(folders.map(folder => folder.name), ['case_13', 'case_15', 'Empty']);
  const group = await readClaimantFolder(folders[0]);
  assert.equal(group.files.length, 1);
  assert.deepEqual(reads, ['Batch', 'case_13', 'Evidence', 'doc.pdf']);
  assert.ok((await readClaimantFolder(folders[2])).problem);
});

test('loose files stay a single request; ZIPs are separate keyboard-selectable packages', () => {
  const files = ['request.xml','record.pdf','ignored.csv'].map(name => new File(['data'], name));
  const [single] = selectedPackages(files);
  assert.equal(single.files.length, 2); assert.equal(single.skipped, 1);
  assert.ok(selectedPackages([files[1], files[1]])[0].problem);
  const zips = ['one.zip', 'two.ZIP'].map(name => new File(['zip'], name));
  assert.equal(selectedPackages(zips).length, 2);
  assert.throws(() => selectedPackages([...files, ...zips]), /Do not mix/);
});
