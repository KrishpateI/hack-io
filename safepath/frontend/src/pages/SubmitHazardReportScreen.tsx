import { ReportForm, type ReportFormProps } from '../components/community/ReportForm';
import { NoDispatchNotice } from '../components/community/NoDispatchNotice';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';

/** Full-screen hazard report form (Req 23.6). Closes after a successful submission. */
export function SubmitHazardReportScreen({ onClose, onCreated, ...form }: ReportFormProps & { onClose: () => void }) {
  return (
    <ScreenFrame title={screenTitle('submit-report')} onClose={onClose} testId="screen-submit-report">
      <NoDispatchNotice />
      <ReportForm
        {...form}
        onCreated={(r) => {
          onCreated?.(r);
          onClose();
        }}
      />
    </ScreenFrame>
  );
}
