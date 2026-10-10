import Icon, { MetaLine } from "./Icon";
import CalendarExportButton from "./CalendarExportButton";
import { SourceLink } from "./CampusCalendar";

/** 對應 kind === "calendar_export"：下載 .ics 的按鈕跟匯入步驟（export_calendar_file）。 */
export default function CalendarExport({ data }) {
  return (
    <div>
      <div className="ncux-card-title ncux-with-icon">
        <Icon name="calendar" size={18} />
        <span>{data.title}</span>
      </div>
      <div className="ncux-card">
        <MetaLine icon="calendar">整年的校曆（{data.calendar_events} 件事，放假、考試、選課、截止日）</MetaLine>
        {data.logged_in ? (
          <>
            <MetaLine icon="book">
              {data.term
                ? `這學期每週的課（${data.term[0]}到${data.term[1]}，放假、停課的日子會跳過）`
                : "每週的課（現在是寒暑假，校曆上還沒有下學期的上課日期，這次不會排進課表）"}
            </MetaLine>
            <MetaLine icon="pin">已報名的活動場次</MetaLine>
          </>
        ) : (
          <MetaLine icon="user">登入之後還可以加上這學期的課跟已報名的活動</MetaLine>
        )}
        <CalendarExportButton />
      </div>
      <div className="ncux-card calendar-export-steps">
        <div className="ncux-card-title">怎麼匯入</div>
        <ol>
          <li>Google 日曆：用電腦開 Google 日曆，右上角「設定」裡的「匯入與匯出」，選剛下載的檔案。手機的 Google 日曆會自動同步。</li>
          <li>iPhone、Mac：直接打開檔案，選「全部加入」。</li>
          <li>Outlook：「檔案」裡的「開啟與匯出」，選「匯入/匯出」。</li>
        </ol>
        <p className="ncux-card-meta">
          建議匯入到另外新增的行事曆（例如「中央大學」），加退選後課表有變時，整個刪掉再重新匯入比較乾淨。
        </p>
      </div>
      <SourceLink source={data.source} />
    </div>
  );
}
