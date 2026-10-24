import { AssistanceRequest } from '../components/community/AssistanceRequest';
import { MessageChannel } from '../components/community/MessageChannel';
import { ReportList } from '../components/community/ReportList';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';
import type { CommunityFeed } from '../components/shell/RightSidebar';

export interface CommunityCommunicationsScreenProps {
  incidentId: string;
  feed: CommunityFeed;
  onClose: () => void;
}

/** Full-screen community channel; official notices are shown in their own panel above community content. */
export function CommunityCommunicationsScreen({ incidentId, feed, onClose }: CommunityCommunicationsScreenProps) {
  return (
    <ScreenFrame title={screenTitle('community')} onClose={onClose} testId="screen-community">
      <MessageChannel incidentId={incidentId} messages={feed.messages} />
      <AssistanceRequest incidentId={incidentId} />
      <ReportList reports={feed.reports} />
    </ScreenFrame>
  );
}
