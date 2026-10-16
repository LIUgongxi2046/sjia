import type { ApplicationDeclaration } from '../../api/application';

export const declaredRelationshipLabels: Record<ApplicationDeclaration['declared_relationship'], string> = {
  mother: '母亲', father: '父亲', other_guardian: '其他监护人',
};

export function ApplicationDeclarationDetails({ declaration }: { declaration: ApplicationDeclaration | null | undefined }) {
  return <section aria-label="申请人自述（未经核验）">
    <h3>申请人自述（未经核验）</h3>
    {declaration ? <>
      <div className="pair"><span>自述患儿称呼</span><strong>{declaration.declared_patient_name}</strong></div>
      <div className="pair"><span>自述与患儿关系</span><strong>{declaredRelationshipLabels[declaration.declared_relationship]}</strong></div>
      <p>提交时已确认所申请资料属于本人监护范围。</p>
    </> : <p>此申请未提供结构化自述，不能从申请说明推定患儿称呼或关系。</p>}
    <p>以上为申请人声明，不代表身份或监护资格已核验，也不构成资料访问授权。关联证明和访问范围仍按正式核验流程确认。</p>
  </section>;
}
