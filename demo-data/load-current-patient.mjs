export async function loadCurrentPatient(baseUrl) {
  const root = new URL(baseUrl);
  const indexResponse = await fetch(new URL('index.json', root));
  if (!indexResponse.ok) throw new Error(`病例索引读取失败：${indexResponse.status}`);
  const index = await indexResponse.json();
  const caseUrl = new URL(index.current_patient_ref, root);
  if (!caseUrl.href.startsWith(root.href)) throw new Error('病例路径超出数据目录');
  const response = await fetch(caseUrl);
  if (!response.ok) throw new Error(`病例资料读取失败：${response.status}`);
  const patientCase = await response.json();
  if (patientCase.patient.id !== index.current_patient_id) throw new Error('当前患者标识不一致');
  if (patientCase.schema_version !== '1.1.0') throw new Error('病例数据版本不受支持');
  const sources = new Map(patientCase.sources.map(source => {
    const url = new URL(source.path, caseUrl);
    if (!url.href.startsWith(new URL('.', caseUrl).href)) throw new Error('来源路径超出病例目录');
    return [source.id, { ...source, url: url.href }];
  }));
  return { patientCase, sources };
}
