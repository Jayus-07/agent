package com.agent.cs.config;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Web 配置：内部接口鉴权过滤器注册 + CORS（网关未启用时直连调试用）
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Bean
    public FilterRegistrationBean<InternalTokenFilter> internalTokenFilter(AppProperties props) {
        FilterRegistrationBean<InternalTokenFilter> reg = new FilterRegistrationBean<>(new InternalTokenFilter(props));
        reg.addUrlPatterns("/internal/*");
        reg.setOrder(1);
        return reg;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        // 生产环境 CORS 由 API Gateway 统一处理，这里仅覆盖直连调试场景
        registry.addMapping("/**")
                .allowedOriginPatterns("*")
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .maxAge(3600);
    }
}
