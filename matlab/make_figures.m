function make_figures()
% MATLAB R2024b. Figures use frozen source data and invoke no optimization.
base = fileparts(fileparts(mfilename('fullpath')));
out = fullfile(base,'figures');
if ~exist(out,'dir'), mkdir(out); end
set(groot,'defaultFigureVisible','off','defaultAxesFontName','Arial', ...
    'defaultAxesFontSize',12,'defaultTextFontName','Arial', ...
    'defaultAxesLineWidth',0.8,'defaultLineLineWidth',1.6, ...
    'defaultAxesTickDir','out','defaultAxesBox','off','defaultAxesTitleFontWeight','normal');
blue=[0.12 0.36 0.62]; orange=[0.80 0.40 0.16]; teal=[0.15 0.57 0.53]; gray=[0.4 0.4 0.4];
data=readtable(fullfile(base,'source_data','figure2_case_trajectories.csv'),'TextType','string');
data=data(data.case_id=="TPL-G4-1-E08",:);
f=figure('Color','w','Units','centimeters','Position',[2 2 17.5 18]);
tl=tiledlayout(f,3,1,'TileSpacing','compact','Padding','compact');
ax=nexttile;hold(ax,'on');
methods=["published","economic","alpha_0p1","alpha_0p3"];
labels=["Published","Economic reference","Repair: \alpha=0.1","Repair: \alpha=0.3"];
colors=[gray;orange;teal;blue];
for j=1:numel(methods)
    r=data(data.method==methods(j)&data.slot<96,:);
    styles={'--',':','-.','-'};ls=styles{j};
    stairs(r.time_h,r.total_q,'Color',colors(j,:),'LineStyle',ls,'LineWidth',1.8,'DisplayName',labels(j));
end
xline(12.5,':','HandleVisibility','off');
xlim([12 13]);ylim([-27 -2]);ylabel('Net injection (kW)');title('(a) Aggregate schedule');xticks(12:.25:13);xticklabels({'12:00','12:15','12:30','12:45','13:00'});
r=data(data.method=="published"&data.slot<96,:);
stairs(r.time_h,r.total_q,'--','Color',gray,'LineWidth',1.1,'HandleVisibility','off'); % Overlay the true coincident published trajectory.
text(12.52,-23,'Deadline','HorizontalAlignment','left','VerticalAlignment','bottom','FontSize',10);
grid on;
ax=nexttile;hold(ax,'on');r=data(data.method=="alpha_0p3"&data.slot<96,:);
ec=[orange;blue;teal;0.5 0.45 0.65];
edgeStyles={'-','-','--',':'};
for j=1:4
    field="delta_q_"+string(char('A'+j-1));
    stairs(r.time_h,r.(field),'Color',ec(j,:),'LineStyle',edgeStyles{j},'DisplayName',"Edge "+string(char('A'+j-1)));
end
yline(0,':','HandleVisibility','off');xline(12.5,':','HandleVisibility','off');
xlim([12 13]);ylim([-23 23]);ylabel('Injection change (kW)');title('(b) Edge compensation (\alpha=0.3)');xticks(12:.25:13);xticklabels({'12:00','12:15','12:30','12:45','13:00'});
legend('Location','northeast','NumColumns',2,'Box','off','FontSize',10);grid on;
ax=nexttile;hold(ax,'on');
for j=1:numel(methods)
    r=data(data.method==methods(j),:);
    styles={'--',':','-.','-'};ls=styles{j};
    plot(r.time_h,r.soc_b_standby,'Color',colors(j,:),'LineStyle',ls,'LineWidth',1.8,'DisplayName',labels(j));
end
plot(24,40,'kd','MarkerFaceColor','w','MarkerSize',7,'HandleVisibility','off');
text(23.8,40,'Terminal requirement: 40%','HorizontalAlignment','right','VerticalAlignment','bottom','FontSize',10);
yline(20,':','Operating minimum','Color',gray,'HandleVisibility','off');
xlim([12 24]);ylim([15 65]);xlabel('Time of day (h)');ylabel('SOC (%)');
title('(c) Standby SOC');
lg=legend('Location','southoutside','NumColumns',2,'Box','off','FontSize',11);lg.Layout.Tile='south';
r=data(data.method=="published",:);plot(r.time_h,r.soc_b_standby,'--','Color',gray,'LineWidth',1.1,'HandleVisibility','off');grid on;
saveFigure(f,out,'figure2_internal_compensation');

s=readtable(fullfile(base,'tables','table4_comparison_summary.csv'),'TextType','string');
s=s(s.scope=="proven_optimal"&s.budget~="ALL",:);
f=figure('Color','w','Units','centimeters','Position',[2 2 17.5 19]);
tl=tiledlayout(f,3,2,'TileSpacing','compact','Padding','compact');
tl.OuterPosition=[0 .02 1 .94];
for j=1:2
    if j==1,b="tracking";prefix="Tracking";else,b="scalar";prefix="Normalized scalar";end
    r=sortrows(s(s.baseline==b,:),'alpha');
    own={r.own_d,r.own_extra,r.own_n_001}; bl={r.baseline_d,r.baseline_extra,r.baseline_n_001};
    yl={'Mean D (kWh)','Mean extra cost (CNY)','Mean Edge count'};
    names={'Aggregate modification','Realized extra cost','Coordination at 0.01 kW'};
    for k=1:3
        ax=nexttile((k-1)*2+j);hold(ax,'on');
        hb=plot(r.alpha,bl{k},'s--','Color',orange,'MarkerFaceColor','w','DisplayName',prefix);
        hp=plot(r.alpha,own{k},'o-','Color',blue,'MarkerFaceColor',blue,'DisplayName','Proposed');
        ylabel(yl{k});xticks([0 .1 .3]);xlim([-.015 .315]);
        if k==1,title(sprintf('(%c) %s',char('a'+j-1),prefix));else,title(sprintf('(%c)',char('a'+(k-1)*2+j-1)));end
        if j==1&&k==1,legendAx=ax;legendHandles=[hb hp];end
        grid on;
        if k==1
            if j==1,nt='Pairs: 55 / 55 / 56';else,nt='Pairs: 60 at each budget';end
            text(.03,.07,nt,'Units','normalized','FontSize',10);
        end
        if k==3,text(.03,.92,'0.01 kW threshold','Units','normalized','FontSize',10);end
        if k==1,ylim([3 5.6]);end
        if k==2,ylim([-.02 .65]);end
        if k==3,ylim([0 1.05]);end

    end
end
xlabel(tl,'Budget ratio \alpha','FontSize',12,'FontName','Arial');
lg=legend(legendAx,legendHandles,{'Comparison baseline','Proposed'},'Orientation','horizontal','Box','off','FontSize',11);lg.Layout.Tile='north';
saveFigure(f,out,'figure3_method_comparison');

a=readtable(fullfile(base,'tables','table5_ablation_summary.csv'),'TextType','string');
p=readtable(fullfile(base,'source_data','figure4_ablation_pairs.csv'),'TextType','string');
f=figure('Color','w','Units','centimeters','Position',[2 2 17.5 14]);
tiledlayout(f,2,2,'TileSpacing','compact','Padding','compact');
nexttile;hold on;values=[a.d(1),a.d(a.method=="without_plan")];
for j=1:2, rectangle('Position',[j-.4,0,.8,values(j)],'FaceColor',colorsForBar(j,blue,orange),'EdgeColor','k','LineWidth',.5); end
xlim([.5 2.5]);xticks([1 2]);xticklabels({'Full','Without D'});ylabel('Mean D (kWh)');
title('(a) Schedule preservation');ylim([0 38]);text([1 2],[4.4171 29.8177]+.6,{'4.42','29.82'},'HorizontalAlignment','center','VerticalAlignment','bottom','FontSize',11);text(1.5,35.5,'+575.05% vs full','HorizontalAlignment','center','FontSize',11);box off;
nexttile;hold on;values=[a.n(1),a.n(a.method=="without_coordination")];
for j=1:2, rectangle('Position',[j-.4,0,.8,values(j)],'FaceColor',colorsForBar(j,blue,orange),'EdgeColor','k','LineWidth',.5); end
xlim([.5 2.5]);xticks([1 2]);xticklabels({'Full','Without N'});ylabel('Mean N');
title('(b) Coordination selection');ylim([0 2.95]);text([1 2],[.55 2.2611]+.06,{'0.550','2.261'},'HorizontalAlignment','center','VerticalAlignment','bottom','FontSize',11);text(1.5,2.76,'+311.11% vs full','HorizontalAlignment','center','FontSize',11);box off;
nexttile([1 2]);r=p(p.variant=="without_final_cost",:);delta=sort(r.ablated_cost-r.full_cost);
delta(abs(delta)<1e-7)=0;
bar(1:height(r),delta,1,'FaceColor',blue,'EdgeColor','none');hold on;
plot(find(delta==0),zeros(sum(delta==0),1),'.','Color',gray,'MarkerSize',5);
yline(mean(delta),'--','Mean: 0.192 CNY','Color',orange,'LabelHorizontalAlignment','left');
xlim([0 181]);ylim([-.05 2.15]);xlabel('Paired configurations (sorted)');ylabel('Extra cost (CNY)');
title('(c) Final cost refinement');text(.03,.89,{'Full method: lower in 52; tied in 128'},'Units','normalized','FontSize',10);box off;
saveFigure(f,out,'figure4_module_ablation');

d=readtable(fullfile(base,'source_data','figure5_standby_prediction.csv'),'TextType','string');
h=readtable(fullfile(base,'source_data','figure5_thresholds.csv'),'TextType','string');
f=figure('Color','w','Units','centimeters','Position',[2 2 17.5 12]);
tl=tiledlayout(f,1,2,'TileSpacing','compact','Padding','compact');
tl.OuterPosition=[0 .15 1 .80];
prices=[1.01 1.10 1.20];cc=[blue;teal;orange];feeStyles={'-','--','-.'};feeMarkers={'o','s','d'};
for j=1:2
    ax=nexttile;hold(ax,'on');
    if j==1,feeLegendAx=ax;feeHandles=gobjects(1,3);end
    if j==1,profile="ORIGINAL";nm='Original profile';else,profile="DAY_SURPLUS";nm='Day-surplus profile';end
    for k=1:3
        r=sortrows(d(d.profile==profile&abs(d.price-prices(k))<1e-8&d.level==1,:),'alpha');
        z=h(h.profile==profile&abs(h.price-prices(k))<1e-8,:);
        fineAlpha=linspace(0,.6,601);epsilon=fineAlpha*.8*8;
        originalExport=(z.analytic_extra_cost-(prices(k)-.8)*8)/.6;
        h0=8-originalExport;
        support=epsilon/(prices(k)-.8);
        second=epsilon>(prices(k)-.8)*h0;
        support(second)=(epsilon(second)+.6*h0)/(prices(k)-.2);
        analyticD=8-min(8,max(0,support));
        plot(fineAlpha,analyticD,feeStyles{k},'Color',cc(k,:),'HandleVisibility','off');
        plot(r.alpha,r.measured_d,feeMarkers{k},'Color',cc(k,:),'MarkerSize',4.5,'MarkerFaceColor','w', ...
            'DisplayName',sprintf('%.2f CNY/kWh',prices(k)));
        plot(z.analytic_alpha,0,'v','Color',cc(k,:),'MarkerFaceColor',cc(k,:),'MarkerSize',7,'HandleVisibility','off');
        if j==1
            feeHandles(k)=plot(nan,nan,'LineStyle',feeStyles{k},'Marker',feeMarkers{k},'Color',cc(k,:),'MarkerFaceColor','w','DisplayName',sprintf('%.2f CNY/kWh',prices(k)));
        end
        text(z.analytic_alpha,-.62,sprintf('%.4f',z.analytic_alpha),'Color',cc(k,:),'HorizontalAlignment','center','FontSize',10);
    end
    xlim([0 .64]);ylim([-.85 8.3]);xticks(0:.1:.6);xlabel('Budget ratio \alpha');ylabel('Aggregate modification D (kWh)');
    title(sprintf('(%c) %s',char('a'+j-1),nm));grid on;

end
lg=legend(feeLegendAx,feeHandles,'Orientation','horizontal','Box','off','FontSize',11);lg.Layout.Tile='north';
annotation(f,'textbox',[.02 .005 .96 .06],'String','Lines: analytical | Open symbols: solved | Triangles: \alpha^*', ...
    'EdgeColor','none','HorizontalAlignment','center','FontSize',11);
saveFigure(f,out,'figure5_budget_price_sensitivity');
fprintf('Created four numerical figures with PNG, vector PDF and editable FIG outputs.\n');
end

function saveFigure(f,out,name)
set(findall(f,'-property','FontName'),'FontName','Arial');
drawnow;
exportgraphics(f,fullfile(out,[name '.png']),'Resolution',300); % Preview only; submit vector PDF.
exportgraphics(f,fullfile(out,[name '.pdf']),'ContentType','vector');
savefig(f,fullfile(out,[name '.fig']));
close(f);
end

function c=colorsForBar(j,blue,orange)
if j==1, c=blue; else, c=orange; end
end
