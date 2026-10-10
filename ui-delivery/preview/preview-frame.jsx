import React, {useEffect, useRef, useState} from 'react';

export function PreviewFrame({capture,children}) {
  const frame=useRef(null);
  const [scale,setScale]=useState(1);
  useEffect(()=>{
    if(capture)return;
    const observer=new ResizeObserver(([entry])=>{
      const next=Math.min(1,(entry.contentRect.width-24)/390,(entry.contentRect.height-24)/844);
      if(next<=0)throw new Error('预览区域尺寸不足');
      setScale(next);
    });
    observer.observe(frame.current);
    return ()=>observer.disconnect();
  },[capture]);
  return <div className="phone-preview" ref={frame} style={{'--preview-scale':scale}}>{children}</div>;
}
