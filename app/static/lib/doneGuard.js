// doneGuard.js — '완료' 전이 앞에 서는 가드.
//
// Task(또는 Epic)를 완료로 보내려는데 **직계 하위에 미완료가 남아 있으면**, 진행하기 전에
// 미완료 하위 목록을 카드 행으로 보여 주며 확인을 받는다. Jira 는 이 상황을 막지 않지만
// (하위가 열려 있어도 부모 완료 가능), 대개는 실수라서 한 번 세운다.
// 하위를 자동으로 같이 완료하지는 않는다 — 하위 Task 는 개별 완료처리가 필요하다.
import { api } from "./api.js";
import { confirmBox } from "./confirm.js";

/** 완료 전이를 계속해도 되는가. 미완료 하위가 없으면 조용히 true, 있으면 확인 팝업.
 *  (children 조회 실패 시에도 막지 않는다 — 가드는 보조 장치지 관문이 아니다.) */
export async function confirmDoneDespiteOpenSubs(key) {
  let kids = [];
  try { kids = (await api.ticketChildren(key)) || []; } catch (e) { kids = []; }
  const open = kids.filter((k) => k.statusCategory !== "done");
  if (!open.length) return true;
  return confirmBox("아직 하위 Task가 완료되지 않았습니다.\n그래도 이 티켓을 완료처리 하시겠습니까?", {
    okLabel: "그래도 완료", cancelLabel: "취소",
    tickets: open.map((k) => ({ key: k.key, summary: k.summary, type: k.type, assignee: k.assignee })),
    note: "하위 Task는 개별 완료처리가 필요합니다",
  });
}
