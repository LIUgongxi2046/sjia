import React, {createContext,useContext,useEffect,useState} from 'react';

const DocumentsContext=createContext(null);
const databaseName='sjia-original-documents';

export function DocumentProvider({children}) {
  const [documents,setDocuments]=useState([]);
  const [database,setDatabase]=useState(null);
  const [storageError,setStorageError]=useState('');
  useEffect(()=>{
    const request=indexedDB.open(databaseName,1);
    request.onupgradeneeded=()=>request.result.createObjectStore('documents',{keyPath:'id'});
    request.onerror=()=>setStorageError('无法打开设备存储，请检查浏览器存储权限。');
    request.onblocked=()=>setStorageError('设备存储正在更新，请关闭其他预览标签后重新打开。');
    request.onsuccess=()=>{
      const db=request.result;
      const read=db.transaction('documents').objectStore('documents').getAll();
      read.onerror=()=>setStorageError('无法读取已保存的原图，请重新打开页面。');
      read.onsuccess=()=>{setDocuments(read.result);setDatabase(db);};
    };
  },[]);
  function saveDocument(document,onComplete) {
    if(!database)throw new Error('设备存储尚未就绪');
    setStorageError('');
    const transaction=database.transaction('documents','readwrite');
    transaction.objectStore('documents').put(document);
    transaction.onabort=()=>setStorageError('保存未完成，请检查设备剩余空间与存储权限，照片仍保留在当前页面。');
    transaction.oncomplete=()=>{
      const read=database.transaction('documents').objectStore('documents').get(document.id);
      read.onerror=()=>setStorageError('保存结果尚未确认，请重新打开页面核对。');
      read.onsuccess=()=>{
        if(!read.result)throw new Error('保存后未读取到原始资料');
        setDocuments(current=>[read.result,...current.filter(item=>item.id!==document.id)]);
        onComplete(read.result);
      };
    };
  }
  return <DocumentsContext.Provider value={{documents,ready:!!database,storageError,saveDocument}}>{children}</DocumentsContext.Provider>;
}

export function useDocuments() {
  const context=useContext(DocumentsContext);
  if(!context)throw new Error('原图资料上下文尚未初始化');
  return context;
}
