import * as apiConfig from "./apiConfig.js";
import { apiErrorMessage } from "./youtubeExportView.js";

SideBEqProvider.configure({ ...apiConfig, apiErrorMessage });
